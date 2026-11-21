package app.scrivo;

import android.app.*;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.os.IBinder;

/** Shell-only, finite activation; no screen or microphone is opened. */
public class ReadyService extends Service {
    public IBinder onBind(Intent intent){return null;}
    public int onStartCommand(Intent intent,int flags,int id){
        getSharedPreferences("dictation",MODE_PRIVATE).edit().putString("state","Ready").commit();
        NotificationManager nm=getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("activation","Dictation connection",NotificationManager.IMPORTANCE_LOW));
        startForeground(2,new Notification.Builder(this,"activation").setSmallIcon(R.drawable.microphone).setContentTitle("Connecting dictation").build(),ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC);
        // Keep the cold process eligible while Android binds accessibility.
        // Ending immediately can race the asynchronous system binding.
        final android.os.Handler main=new android.os.Handler(getMainLooper());
        final long deadline=android.os.SystemClock.uptimeMillis()+15000;
        main.post(new Runnable(){public void run(){
            if(TextTargetService.current!=null||android.os.SystemClock.uptimeMillis()>=deadline){
                stopForeground(STOP_FOREGROUND_REMOVE);stopSelf(id);
            }else main.postDelayed(this,250);
        }});
        return START_NOT_STICKY;
    }
}
