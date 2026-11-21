package app.scrivo;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
/** Words go to the selected text box; the clipboard is only for when there is none. */
final class TextDelivery {
    static boolean deliver(Context context,TextTargetService.Target target,String words){
        if(target!=null&&target.insert(words))return true;
        context.getSystemService(ClipboardManager.class).setPrimaryClip(ClipData.newPlainText("Dictation",words));
        return false;
    }
}
