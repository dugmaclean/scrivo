package app.scrivo;

import java.io.*;
import java.net.*;
import java.util.Arrays;
import java.util.UUID;
import java.util.concurrent.*;
import java.util.function.Consumer;
import org.json.JSONObject;

/** Upload PCM during capture; one bounded, private preview reader per recording. */
final class LiveUpload {
    /** Room for long dictations: an hour of speech is about 60,000 characters. */
    static final int REPLY_LIMIT=4_000_000,PREVIEW_LIMIT=1_000_000;
    /** Silence allowed while the server finishes and tidies a long dictation. */
    static final int RESULT_WAIT_MS=15*60*1000;
    private final ArrayBlockingQueue<byte[]> chunks=new ArrayBlockingQueue<>(32);
    private final CountDownLatch finished=new CountDownLatch(1);
    private volatile boolean failed,cancelled,previewStopped;
    private volatile HttpURLConnection connection,previewConnection;
    private final String endpoint,token,writer,session=UUID.randomUUID().toString().replace("-","");
    private volatile Consumer<String> listener;
    private Thread previewThread;
    LiveUpload(String endpoint,String token,String writer){this.endpoint=endpoint;this.token=token;this.writer=writer;}
    void setPreviewListener(Consumer<String> value){listener=value;}
    void offer(byte[] chunk,int count){if(!failed&&!cancelled&&!chunks.offer(Arrays.copyOf(chunk,count))){failed=true;HttpURLConnection c=connection;if(c!=null)c.disconnect();stopPreview();}}
    void finish(boolean send){cancelled=!send;finished.countDown();stopPreview();if(cancelled){HttpURLConnection c=connection;if(c!=null)c.disconnect();}chunks.offer(new byte[0]);}
    private void stopPreview(){previewStopped=true;HttpURLConnection c=previewConnection;if(c!=null)c.disconnect();Thread t=previewThread;if(t!=null)t.interrupt();}
    private static String readReply(InputStream input) throws IOException {
        ByteArrayOutputStream body=new ByteArrayOutputStream();byte[] block=new byte[4096];int n;
        while((n=input.read(block))!=-1){if(body.size()+n>REPLY_LIMIT)throw new IOException();body.write(block,0,n);}
        return body.toString("UTF-8");
    }
    private void previews(){
        String previous="";int failures=0;
        while(!previewStopped&&!cancelled&&!failed&&finished.getCount()!=0){
            HttpURLConnection c=null;
            try{
                URL url=new URL(endpoint+"/v1/streams/"+session);
                if(!"https".equals(url.getProtocol())||url.getUserInfo()!=null)break;
                c=(HttpURLConnection)url.openConnection();previewConnection=c;
                c.setInstanceFollowRedirects(false);c.setConnectTimeout(2000);c.setReadTimeout(2000);c.setUseCaches(false);
                c.setRequestProperty("Authorization","Bearer "+token);
                if(previewStopped||finished.getCount()==0)break;
                if(c.getResponseCode()==200){
                    String text;
                    try(InputStream input=c.getInputStream()){text=new JSONObject(readReply(input)).optString("text");}
                    if(text.length()>PREVIEW_LIMIT)throw new IOException();
                    failures=0;
                    Consumer<String> callback=listener;
                    if(!text.isEmpty()&&!text.equals(previous)&&!previewStopped&&!cancelled&&!failed&&finished.getCount()!=0&&callback!=null){callback.accept(text);previous=text;}
                }else if(++failures>=3)break;
            }catch(Exception unavailable){if(++failures>=3)break;}
            finally{if(c!=null)c.disconnect();previewConnection=null;}
            try{Thread.sleep(500);}catch(InterruptedException stop){break;}
        }
    }
    JSONObject run() throws InterruptedException {
        JSONObject result=null;
        try{
            URL url=new URL(endpoint+"/v1/stream?writer="+writer+"&session="+session);
            if(!"https".equals(url.getProtocol())||url.getUserInfo()!=null)throw new IOException();
            connection=(HttpURLConnection)url.openConnection();connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(10000);connection.setReadTimeout(RESULT_WAIT_MS);connection.setRequestMethod("POST");connection.setDoOutput(true);connection.setChunkedStreamingMode(4096);connection.setRequestProperty("Authorization","Bearer "+token);connection.setRequestProperty("Content-Type","audio/pcm;rate=16000;channels=1");
            if(writer.equals("haiku"))connection.setRequestProperty("X-Allow-Cloud","true");
            try(OutputStream output=connection.getOutputStream()){
                if(listener!=null&&!previewStopped){previewThread=new Thread(this::previews,"dictation-preview");previewThread.start();}
                while(!failed&&!cancelled){
                    byte[] chunk=chunks.poll(250,TimeUnit.MILLISECONDS);
                    if(chunk!=null&&chunk.length>0){output.write(chunk);output.flush();}
                    if(finished.getCount()==0&&chunks.isEmpty())break;
                }
                if(failed||cancelled)throw new IOException();
            }
            if(connection.getResponseCode()!=200)throw new IOException();
            try(InputStream input=connection.getInputStream()){result=new JSONObject(readReply(input));}
        }catch(Exception unavailable){failed=true;}
        finally{
            stopPreview();
            HttpURLConnection c=connection;if(c!=null)c.disconnect();
            // Bounded network timeouts and disconnect let this join end; no detached reader.
            if(previewThread!=null)previewThread.join();
        }
        // An early connection failure must never retry the saved file during capture.
        finished.await();
        return cancelled?null:result;
    }
}
