package app.scrivo;

import android.app.*;
import android.content.*;
import android.content.pm.ServiceInfo;
import android.graphics.PixelFormat;
import android.media.*;
import android.os.*;
import android.view.*;
import android.widget.*;
import java.io.*;
import java.net.*;
import java.nio.*;
import java.util.UUID;
import java.util.concurrent.*;
import org.json.JSONObject;

public class BubbleService extends Service {
    /** The server writes with the three models saved there, shared by every device. */
    static final String WRITER="saved";
    /** True once a server address and access token are saved; the settings screen asks for them. */
    static boolean connected(Context context){
        android.content.SharedPreferences p=context.getSharedPreferences("dictation",MODE_PRIVATE);
        return p.getString("endpoint","").startsWith("https://")&&p.getString("token","").length()>=32;
    }
    public static boolean prepare(Context context){
        android.content.SharedPreferences p=context.getSharedPreferences("dictation",MODE_PRIVATE);
        if(!p.getBoolean("bubble",true))return false;
        if(!connected(context)
            ||context.checkSelfPermission(android.Manifest.permission.RECORD_AUDIO)!=android.content.pm.PackageManager.PERMISSION_GRANTED
            ||!android.provider.Settings.canDrawOverlays(context))return false;
        TextTargetService.refreshBubble();return TextTargetService.current!=null;
    }
    public static void updateVisibility(){TextTargetService.refreshBubble();}
    private final Handler main=new Handler(Looper.getMainLooper());
    private final ExecutorService network=Executors.newSingleThreadExecutor();
    private volatile boolean recording;
    private boolean processing,stopping,destroyed;
    private AudioRecord recorder;
    private Thread reader;
    private RecordingFile audioFile;
    private LiveUpload liveUpload;
    private volatile long generation;
    private TextTargetService.Target target;
    public IBinder onBind(Intent intent){return null;}

    public void onCreate(){
        super.onCreate();
        getSharedPreferences("dictation",MODE_PRIVATE).edit().putString("state","Ready").apply();
        NotificationManager nm=getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("dictation",getString(R.string.app_name),NotificationManager.IMPORTANCE_LOW));
        startForeground(1,notification("Ready to dictate."),ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE);
    }
    private Notification notification(String text){
        PendingIntent open=PendingIntent.getActivity(this,0,new Intent(this,MainActivity.class),PendingIntent.FLAG_IMMUTABLE|PendingIntent.FLAG_UPDATE_CURRENT);
        return new Notification.Builder(this,"dictation").setSmallIcon(R.drawable.microphone).setContentTitle(getString(R.string.app_name)).setContentText(text).setContentIntent(open).setOngoing(true).build();
    }
    private void show(String text){if(destroyed)return;TextTargetService.state(text);getSharedPreferences("dictation",MODE_PRIVATE).edit().putString("state",text).apply();getSystemService(NotificationManager.class).notify(1,notification(text));}
    private void startRecording(boolean inApp){
        if(recording||processing||stopping)return;
        if(!connected(this)){
            String words="Open Scrivo and enter your server address and access token first.";
            show(words);Toast.makeText(this,words,Toast.LENGTH_LONG).show();return;
        }
        target=inApp?null:TextTargetService.capture();
        if(!inApp&&TextTargetService.sensitive()){show("Dictation is hidden for private fields");return;}
        File folder=new File(getFilesDir(),"recordings");folder.mkdirs();
        RecordingFile.tidy(folder,64_000_000,System.currentTimeMillis());
        if(folder.getUsableSpace()<RecordingFile.MIN_FREE){show("Recording storage is full");return;}
        try{
            int size=Math.max(4096,AudioRecord.getMinBufferSize(16000,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT));
            // The voice recognition source keeps quiet speech that the generic microphone
            // processing loses on some phones.
            recorder=new AudioRecord(MediaRecorder.AudioSource.VOICE_RECOGNITION,16000,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT,size);
            audioFile=new RecordingFile(folder);recorder.startRecording();recording=true;show("Listening");
            final RecordingFile active=audioFile;
            final TextTargetService.Target original=target;
            final long request=++generation;
            android.content.SharedPreferences preferences=getSharedPreferences("dictation",MODE_PRIVATE);
            final LiveUpload upload=new LiveUpload(preferences.getString("endpoint",""),preferences.getString("token",""),WRITER);liveUpload=upload;
            upload.setPreviewListener(text->main.post(()->{
                if(!destroyed&&request==generation&&recording&&!stopping)TextTargetService.preview(text);
            }));
            network.execute(()->{
                try{
                    JSONObject result=upload.run();
                    if(request!=generation)return;
                    if(result!=null)completeResult(active.file,original,result,request);
                    else if(processing)send(active.file,original);
                }catch(Exception error){if(request==generation)main.post(()->{if(request==generation){processing=false;show("Recording saved. Service unavailable.");}});}
            });
            reader=new Thread(()->{
                try{
                    // Small reads keep the sound bars current; the saved file and the upload still move in full blocks.
                    byte[] chunk=new byte[4096];int filled=0;
                    SilenceWatch quiet=new SilenceWatch(SilenceWatch.LIMIT_MS);long blocks=0;
                    while(recording){
                        int n=recorder.read(chunk,filled,Math.min(1024,chunk.length-filled));
                        if(n<0){main.post(()->stopRecording(false));break;}
                        if(n==0)continue;
                        double energy=0;for(int i=filled;i+1<filled+n;i+=2){int v=(short)((chunk[i]&255)|(chunk[i+1]<<8));energy+=(double)v*v;}
                        final float level=(float)Math.min(1,Math.sqrt(energy/Math.max(1,n/2))/4500);main.post(()->TextTargetService.level(level));
                        filled+=n;
                        // A forgotten recording ends only after minutes of complete silence; talking resets it.
                        if(quiet.hear(chunk,filled-n,n,16000)){main.post(()->stopRecording(true));break;}
                        if(filled==chunk.length){
                            upload.offer(chunk,filled);boolean full=active.append(chunk,filled);filled=0;
                            if(++blocks%80==0&&active.file.getUsableSpace()<RecordingFile.MIN_FREE/2)full=true;
                            if(full){main.post(()->stopRecording(true));break;}
                        }
                    }
                    if(filled>0){upload.offer(chunk,filled);active.append(chunk,filled);}
                }catch(IOException error){main.post(()->stopRecording(false));}
                finally{try{active.close();}catch(IOException ignored){}}
            },"dictation-capture");reader.start();
        }catch(RuntimeException|IOException e){recording=false;if(audioFile!=null){try{audioFile.close();}catch(IOException ignored){}}if(recorder!=null){recorder.release();recorder=null;}show("Microphone unavailable");}
    }
    private void stopRecording(boolean send){
        if(!send){generation++;if(liveUpload!=null)liveUpload.finish(false);if(processing){processing=false;show("Ready");}}
        if(!recording)return;recording=false;stopping=true;
        try{recorder.stop();}catch(RuntimeException ignored){}
        finishRecording(send);
    }
    private void finishRecording(boolean send){
        if(reader!=null&&reader.isAlive()){main.postDelayed(()->finishRecording(send),25);return;}
        recorder.release();recorder=null;stopping=false;
        if(audioFile.size()==0){if(liveUpload!=null)liveUpload.finish(false);show("No recording captured");return;}
        File saved=audioFile.file;
        if(!send){if(liveUpload!=null)liveUpload.finish(false);show("Recording saved");return;}
        processing=true;show("Writing");TextTargetService.Target original=target;
        if(liveUpload!=null)liveUpload.finish(true);else network.execute(()->send(saved,original));
    }
    private void send(File saved,TextTargetService.Target original){
        final long request=generation;
        HttpURLConnection connection=null;
        try{
            android.content.SharedPreferences p=getSharedPreferences("dictation",MODE_PRIVATE);
            String writer=WRITER;URL url=new URL(p.getString("endpoint","")+"/v1/dictate?writer="+writer);
            if(!url.getProtocol().equals("https"))throw new IOException();
            connection=(HttpURLConnection)url.openConnection();connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(10000);connection.setReadTimeout(LiveUpload.RESULT_WAIT_MS);connection.setRequestMethod("POST");connection.setDoOutput(true);connection.setRequestProperty("Authorization","Bearer "+p.getString("token",""));connection.setRequestProperty("Content-Type","audio/wav");
            if(writer.equals("haiku"))connection.setRequestProperty("X-Allow-Cloud","true");
            connection.setFixedLengthStreamingMode(saved.length());
            try(InputStream in=new FileInputStream(saved);OutputStream out=connection.getOutputStream()){byte[] chunk=new byte[8192];int read;while((read=in.read(chunk))!=-1)out.write(chunk,0,read);}
            int responseCode=connection.getResponseCode();
            if(responseCode!=200){
                // The retry response confirms recognition failed and the server kept a copy.
                if(responseCode==503){
                    try(InputStream in=connection.getErrorStream()){
                        JSONObject failure=new JSONObject(ServiceClient.readText(in));
                        if("retry".equals(failure.optString("status"))&&failure.optBoolean("recording_saved")){
                            main.post(()->{if(request==generation){processing=false;show("Speech not recognized. Recording saved.");}});
                            return;
                        }
                    }
                }
                throw new IOException();
            }
            ByteArrayOutputStream body=new ByteArrayOutputStream();
            try(InputStream in=connection.getInputStream()){byte[] block=new byte[4096];int n;while((n=in.read(block))!=-1){body.write(block,0,n);if(body.size()>LiveUpload.REPLY_LIMIT)throw new IOException();}}
            JSONObject result=new JSONObject(body.toString("UTF-8"));completeResult(saved,original,result,request);
        }catch(Exception e){main.post(()->{if(request==generation){processing=false;show("Recording saved. Service unavailable.");}});}
        finally{if(connection!=null)connection.disconnect();}
    }
    private void completeResult(File saved,TextTargetService.Target original,JSONObject result,long request) throws Exception {
        final String text=result.getString("text");final boolean raw="raw".equals(result.optString("status"));
        main.post(()->{
            if(destroyed||request!=generation)return;
            getSharedPreferences("dictation",MODE_PRIVATE).edit().putString("last_text",text).apply();
            saved.renameTo(new File(saved.getParentFile(),saved.getName()+".done"));
            processing=false;boolean inserted=TextDelivery.deliver(this,original,text);
            show(!inserted?"Copied to clipboard":raw?"Done — original words kept":"Done");
            if(!inserted)Toast.makeText(this,"Copied to clipboard",Toast.LENGTH_SHORT).show();
        });
    }
    public int onStartCommand(Intent intent,int flags,int startId){
        String command=intent==null?null:intent.getStringExtra("command");
        if(command!=null){
            if(command.equals("bubble_start")||command.equals("bubble_hold"))startRecording(false);
            else if(command.equals("home_start"))startRecording(true);
            else if(command.equals("home_stop"))stopRecording(true);
            else if(command.equals("home_cancel"))stopRecording(false);
            return START_NOT_STICKY;
        }
        String name=intent==null?null:intent.getStringExtra("retry_recording");
        if(name==null)TextTargetService.refreshBubble();
        if(name!=null&&(recording||processing||stopping)){show("Finish the current dictation before recovering another recording");return START_NOT_STICKY;}
        if(name!=null&&name.matches("[a-f0-9-]{36}\\.wav")){
            File saved=new File(new File(getFilesDir(),"recordings"),name);
            if(saved.isFile()){processing=true;show("Recovering saved speech");network.execute(()->send(saved,null));}
            else show("This saved recording is unavailable");
        }
        return START_STICKY;
    }
    public void onDestroy(){generation++;if(liveUpload!=null)liveUpload.finish(false);destroyed=true;if(recording)stopRecording(false);network.shutdownNow();getSharedPreferences("dictation",MODE_PRIVATE).edit().putString("state","Ready").apply();super.onDestroy();}
}
