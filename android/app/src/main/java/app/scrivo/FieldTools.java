package app.scrivo;

import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.text.Spanned;
import android.text.style.*;
import android.util.Log;
import android.view.accessibility.AccessibilityNodeInfo;

/** Android-only helpers for placing words; kept apart so the placing rules can be checked on a computer. */
final class FieldTools {
    private FieldTools(){}
    /** Why the words went where they did. Never the words themselves. */
    static void note(String reason){Log.i("Scrivo","delivery: "+reason);}
    static boolean rich(CharSequence text){
        if(!(text instanceof Spanned))return false;
        Spanned s=(Spanned)text;
        Class<?>[] kinds={StyleSpan.class,URLSpan.class,QuoteSpan.class,BulletSpan.class,StrikethroughSpan.class,
            ForegroundColorSpan.class,BackgroundColorSpan.class,RelativeSizeSpan.class,AbsoluteSizeSpan.class,TypefaceSpan.class};
        for(Class<?> kind:kinds)if(s.getSpans(0,s.length(),kind).length>0)return true;
        return false;
    }
    static boolean inWebPage(AccessibilityNodeInfo node){
        try{
            AccessibilityNodeInfo parent=node.getParent();
            for(int depth=0;parent!=null&&depth<60;depth++){
                CharSequence kind=parent.getClassName();
                if(kind!=null&&kind.toString().endsWith("WebView"))return true;
                parent=parent.getParent();
            }
        }catch(RuntimeException gone){/* unknown: treat as an ordinary box */}
        return false;
    }
    /** The app's own Paste puts the words at its cursor and keeps everything else as it was. */
    static boolean paste(Context context,AccessibilityNodeInfo field,String words){
        ClipboardManager clips=context.getSystemService(ClipboardManager.class);
        ClipData earlier=null;
        try{earlier=clips.getPrimaryClip();}catch(RuntimeException hidden){/* Android hides it from background apps */}
        try{
            clips.setPrimaryClip(ClipData.newPlainText("Dictation",words));
            if(!field.performAction(AccessibilityNodeInfo.ACTION_PASTE))return false;
        }catch(RuntimeException refused){return false;}
        if(earlier!=null){
            final ClipData restore=earlier;
            new Handler(Looper.getMainLooper()).postDelayed(()->{try{clips.setPrimaryClip(restore);}catch(RuntimeException ignored){}},800);
        }
        return true;
    }
}
