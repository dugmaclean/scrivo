package app.scrivo;

/**
 * Ends a forgotten recording. Dictation has no length limit, so a recording left running
 * must still stop itself. Only several minutes without speech count: any spoken word
 * starts the count again.
 *
 * "Without speech" is measured against the room itself. A fixed loudness line does not
 * work: on a phone whose quiet room never fell below it, a recording left alone ran on
 * for minutes of silence without stopping. The watch follows the room's background level
 * (it drops at once to a quieter reading and creeps up only slowly) and counts a moment
 * as speech only when it is clearly louder than that background.
 */
final class SilenceWatch {
    static final long LIMIT_MS=5*60*1000;
    /** Below this average loudness nothing is speech, however quiet the room. */
    static final double QUIET_RMS=250;
    /** Speech must be this many times louder than the room's background. */
    static final double SPEECH_OVER_ROOM=3;
    private final long limitMs;
    private long quietSamples;
    private double room=-1;
    SilenceWatch(long limitMs){this.limitMs=limitMs;}
    /** Feed 16-bit little-endian samples; true once the silence has lasted the whole limit. */
    boolean hear(byte[] data,int offset,int count,int rate){
        int samples=count/2;if(samples<=0)return false;
        double energy=0;
        for(int i=offset;i+1<offset+count;i+=2){int v=(short)((data[i]&255)|(data[i+1]<<8));energy+=(double)v*v;}
        double rms=Math.sqrt(energy/samples);
        if(room<0||rms<room)room=rms;else room+=(rms-room)*0.0005*samples/512;
        if(rms>=Math.max(QUIET_RMS,room*SPEECH_OVER_ROOM))quietSamples=0;else quietSamples+=samples;
        return quietSamples*1000L/rate>=limitMs;
    }
}
