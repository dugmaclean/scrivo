package app.scrivo;
import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
/** Installer-only no-display activation clears Android's force-stopped state. */
public final class ReadyActivity extends Activity {
    protected void onCreate(Bundle state){
        super.onCreate(state);
        startForegroundService(new Intent(this,ReadyService.class));
        finish();
    }
}
