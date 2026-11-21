package app.scrivo;

import android.accessibilityservice.AccessibilityService;
import android.os.Bundle;
import android.view.accessibility.*;

public class TextTargetService extends AccessibilityService {
    static TextTargetService current;
    private DictationOverlay overlay;
    public void onServiceConnected(){current=this;overlay=new DictationOverlay(this);}
    public void onAccessibilityEvent(AccessibilityEvent event){if(overlay!=null)overlay.refresh();}
    static void state(String text){if(current!=null&&current.overlay!=null)current.overlay.state(text);}
    static void preview(String text){if(current!=null&&current.overlay!=null)current.overlay.preview(text);}
    static void level(float value){if(current!=null&&current.overlay!=null)current.overlay.level(value);}
    static void refreshBubble(){if(current!=null&&current.overlay!=null)current.overlay.refresh();}
    public void onInterrupt(){}
    public void onDestroy(){if(overlay!=null)overlay.close();if(current==this)current=null;super.onDestroy();}

    private static AccessibilityNodeInfo inputFocus(AccessibilityNodeInfo root){
        try{return root==null?null:root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);}catch(RuntimeException gone){return null;}
    }
    /**
     * The view that has the cursor right now. Only windows that own input focus are
     * inspected, never a background window's remembered field. A focused system
     * window without content (a taskbar, an edge panel) must not hide the app's field.
     */
    private AccessibilityNodeInfo focusedInput(){
        AccessibilityNodeInfo found=null;
        try{
            for(AccessibilityWindowInfo window:getWindows()){
                if(!window.isFocused())continue;
                AccessibilityNodeInfo node=inputFocus(window.getRoot());
                if(node!=null&&node.isFocused()){found=node;break;}
            }
        }catch(RuntimeException unavailable){/* fall through to the service-wide lookups */}
        if(found==null){
            try{AccessibilityNodeInfo node=findFocus(AccessibilityNodeInfo.FOCUS_INPUT);if(node!=null&&node.isFocused())found=node;}
            catch(RuntimeException unavailable){/* keep looking */}
        }
        if(found==null){
            AccessibilityNodeInfo node=null;
            try{node=inputFocus(getRootInActiveWindow());}catch(RuntimeException unavailable){/* none */}
            if(node!=null&&node.isFocused())found=node;
        }
        return found;
    }
    private static boolean writable(AccessibilityNodeInfo node){return node!=null&&node.isEditable()&&!node.isPassword()&&node.isFocused();}

    /** One dictation started from the floating control; the field it began in is only a hint. */
    static class Target {
        final AccessibilityNodeInfo node;
        Target(AccessibilityNodeInfo n){node=n;}
        /** Put the words at the cursor of the text box that is selected when speaking ends. */
        boolean insert(String words){
            TextTargetService service=current;
            if(service==null||words==null||words.isEmpty())return false;
            AccessibilityNodeInfo field=service.focusedInput();
            if(field==null&&node!=null){
                // Some apps report no input focus through their windows; the field the
                // dictation began in still counts while it says it holds the cursor.
                try{if(node.refresh())field=node;}catch(RuntimeException gone){/* no field */}
            }
            if(field==null){FieldTools.note("no text box selected");return false;}
            if(field.isPassword()){FieldTools.note("private field refused");return false;}
            if(!writable(field)){FieldTools.note("selected view is not a text box");return false;}
            return service.write(field,words);
        }
    }
    private boolean write(AccessibilityNodeInfo field,String words){
        CharSequence raw=field.getText();
        int start=field.getTextSelectionStart(),end=field.getTextSelectionEnd();
        // An empty box often reports its grey hint as its text; that is still an empty box.
        boolean empty=looksEmpty(raw,field.getHintText(),field.getContentDescription(),field.isShowingHintText());
        String text=empty?"":raw.toString();
        if(start>end){int swap=start;start=end;end=swap;}
        // No reported cursor: add the words at the end rather than giving up.
        if(empty){start=0;end=0;}else if(start<0||end>text.length()){start=text.length();end=start;}
        String piece=spaced(text,start,end,words);
        // Replacing all the text would flatten formatted or web-page writing; paste there instead.
        boolean pasteFirst=!empty&&(FieldTools.rich(raw)||(field.isMultiLine()&&FieldTools.inWebPage(field)));
        if(pasteFirst&&FieldTools.paste(this,field,piece)){FieldTools.note("pasted at the cursor");return true;}
        Bundle args=new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,text.substring(0,start)+piece+text.substring(end));
        if(field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,args)){
            int cursor=start+piece.length();
            if(cursor<text.length()-(end-start)+piece.length()){
                Bundle place=new Bundle();
                place.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT,cursor);
                place.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT,cursor);
                field.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION,place);
            }
            FieldTools.note("typed at the cursor");return true;
        }
        if(!pasteFirst&&FieldTools.paste(this,field,piece)){FieldTools.note("pasted after typing was refused");return true;}
        FieldTools.note("text box refused typing and pasting");return false;
    }
    /**
     * Grey hint words such as "Message" or "Send message" that messaging apps report as the
     * box's text while it is empty, often without saying they are a hint. Kept short and
     * whole-text only, so real writing that merely contains these words is never touched.
     */
    private static final java.util.regex.Pattern HINT_WORDS=java.util.regex.Pattern.compile(
        "(?:(?:send|type|write|enter|add|new|text|rcs|sms|mms|chat|signal|encrypted|start)\\s+(?:a\\s+|an\\s+|your\\s+)?)?"
        +"(?:message|reply|comment|caption)(?:\\s*(?:here|\\.\\.\\.|\u2026))?|(?:type|write) (?:here|something)(?:\\s*(?:\\.\\.\\.|\u2026))?",
        java.util.regex.Pattern.CASE_INSENSITIVE);
    /** True when the box holds no words of the writer's own, only its hint or label. */
    static boolean looksEmpty(CharSequence raw,CharSequence hint,CharSequence label,boolean showingHint){
        if(raw==null||raw.length()==0||showingHint)return true;
        String words=raw.toString().trim();
        if(words.isEmpty())return true;
        if(hint!=null&&words.equalsIgnoreCase(hint.toString().trim()))return true;
        if(label!=null&&words.equalsIgnoreCase(label.toString().trim()))return true;
        return words.length()<=40&&HINT_WORDS.matcher(words).matches();
    }
    /** Any kind of space, including the unbreakable one email writing areas put after a word. */
    private static boolean gap(char c){return Character.isWhitespace(c)||Character.isSpaceChar(c);}
    /** Keep dictated words from running into the words on either side of the cursor. */
    static String spaced(String text,int start,int end,String words){
        String piece=words;
        if(start>0&&piece.length()>0){
            char before=text.charAt(start-1),first=piece.charAt(0);
            if(!gap(before)&&"([{\"'/-“‘".indexOf(before)<0&&Character.isLetterOrDigit(first))piece=" "+piece;
        }
        if(end<text.length()&&piece.length()>0){
            char after=text.charAt(end),last=piece.charAt(piece.length()-1);
            if(Character.isLetterOrDigit(after)&&!gap(last))piece=piece+" ";
        }
        return piece;
    }
    static boolean sensitive(){
        TextTargetService service=current;
        if(service==null)return false;
        AccessibilityNodeInfo node=service.focusedInput();
        return node!=null&&node.isPassword();
    }
    /** Begin a floating-control dictation; a text box is not required to start. */
    static Target capture(){
        TextTargetService service=current;
        if(service==null)return null;
        AccessibilityNodeInfo node=service.focusedInput();
        return new Target(writable(node)?node:null);
    }
}
