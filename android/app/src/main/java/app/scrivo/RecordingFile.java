package app.scrivo;

import java.io.*;
import java.nio.*;
import java.util.UUID;

/** A valid WAV is kept on disk as each captured chunk arrives. */
final class RecordingFile implements Closeable {
    /**
     * No time limit: a dictation may run as long as the speaker wants. The only
     * ceiling is the WAV format's own size field, about eighteen hours of speech; the
     * capture loop also stops when the phone runs out of space or hears minutes of silence.
     */
    static final int MAX_PCM=Integer.MAX_VALUE-44-1;
    /** Free phone space a recording needs to start or keep going. */
    static final long MIN_FREE=100_000_000L;
    final File file;
    private final RandomAccessFile output;
    private int size;
    RecordingFile(File folder) throws IOException {
        if(!folder.isDirectory()&&!folder.mkdirs())throw new IOException();
        file=new File(folder,UUID.randomUUID()+".wav");
        output=new RandomAccessFile(file,"rw");
        ByteBuffer header=ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN);
        header.put(new byte[]{'R','I','F','F'}).putInt(36).put(new byte[]{'W','A','V','E','f','m','t',' '})
            .putInt(16).putShort((short)1).putShort((short)1).putInt(16000).putInt(32000)
            .putShort((short)2).putShort((short)16).put(new byte[]{'d','a','t','a'}).putInt(0);
        output.write(header.array());
    }
    boolean append(byte[] data,int count) throws IOException {
        int length=Math.min(count,MAX_PCM-size);length-=length%2;
        output.seek(44L+size);output.write(data,0,length);size+=length;
        output.seek(4);output.write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(size+36).array());
        output.seek(40);output.write(ByteBuffer.allocate(4).order(ByteOrder.LITTLE_ENDIAN).putInt(size).array());
        return size>=MAX_PCM;
    }
    int size(){return size;}
    static final int KEEP_FINISHED=10;
    static final long OLD_MS=7L*24*60*60*1000;
    /**
     * Keep the phone from filling up, and say how many bytes of recordings remain.
     *
     * Without this nothing would ever remove a recording, so after about half an hour of
     * speaking in total the app would answer "Recording storage is full" and refuse to
     * start. A finished recording is already saved on the server, so only the
     * newest few stay here. A recording still waiting to be turned into words is kept;
     * only when there is no room for a new one do waiting ones older than a week go,
     * oldest first.
     */
    static long tidy(File folder,long room,long now){
        File[] files=folder.listFiles();
        if(files==null)return 0;
        java.util.Arrays.sort(files,(a,b)->Long.compare(b.lastModified(),a.lastModified()));
        long used=0;int finished=0;
        java.util.ArrayList<File> waiting=new java.util.ArrayList<>(),done=new java.util.ArrayList<>();
        for(File f:files){
            if(f.getName().endsWith(".wav.done")){if(++finished>KEEP_FINISHED&&f.delete())continue;done.add(f);}
            else if(f.getName().endsWith(".wav"))waiting.add(f);
            used+=f.length();
        }
        for(int i=done.size()-1;i>=0&&used>room;i--){
            File f=done.get(i);long size=f.length();
            if(f.delete())used-=size;
        }
        for(int i=waiting.size()-1;i>=0&&used>room;i--){
            File f=waiting.get(i);long size=f.length();
            if(now-f.lastModified()>OLD_MS&&f.delete())used-=size;
        }
        return used;
    }
    public void close() throws IOException {try{output.getFD().sync();}finally{output.close();}}
}
