"""Run the real Android text target class against stand-in Android classes.

Words go to whichever text box holds the cursor when speaking ends, at the cursor.
The clipboard is only for when no text box is selected. Private fields stay refused.

Needs a JDK 17: java and javac are taken from $JAVA_HOME/bin, then PATH. No Android SDK
is needed. Example:

    JAVA_HOME=/path/to/jdk-17 python3 tools/check_text_target.py
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
SOURCES = {
'app/scrivo/DictationOverlay.java': 'package app.scrivo; class DictationOverlay {DictationOverlay(Object c){} void refresh(){} void state(String s){} void preview(String s){} void level(float f){} void close(){}}',
'app/scrivo/BubbleService.java': 'package app.scrivo; public class BubbleService {public static boolean prepare(Object c){return true;} public static void updateVisibility(){}}',
'app/scrivo/FieldTools.java': '''package app.scrivo; import android.view.accessibility.AccessibilityNodeInfo;
final class FieldTools {static boolean rich,web,pasteWorks=true; static int pastes; static String pasted,last;
 static void note(String reason){last=reason;} static boolean rich(CharSequence t){return rich;} static boolean inWebPage(AccessibilityNodeInfo n){return web;}
 static boolean paste(Object c,AccessibilityNodeInfo field,String words){if(!pasteWorks)return false;pastes++;pasted=words;return true;}}''',
'android/os/Bundle.java': '''package android.os; public class Bundle {public CharSequence text; public java.util.Map<String,Integer> ints=new java.util.HashMap<>(); public void putCharSequence(String k,CharSequence v){text=v;} public void putInt(String k,int v){ints.put(k,v);}}''',
'android/accessibilityservice/AccessibilityService.java': '''package android.accessibilityservice; import android.view.accessibility.*; public class AccessibilityService {public static AccessibilityNodeInfo root,anywhere; public static java.util.List<AccessibilityWindowInfo> windows=new java.util.ArrayList<>(); public java.util.List<AccessibilityWindowInfo> getWindows(){return windows;} public AccessibilityNodeInfo getRootInActiveWindow(){return root;} public AccessibilityNodeInfo findFocus(int kind){return anywhere;} public void onDestroy(){}}''',
'android/view/accessibility/AccessibilityWindowInfo.java': '''package android.view.accessibility; public class AccessibilityWindowInfo {public AccessibilityNodeInfo root; public boolean focused; public AccessibilityWindowInfo(AccessibilityNodeInfo r,boolean f){root=r;focused=f;} public boolean isFocused(){return focused;} public AccessibilityNodeInfo getRoot(){return root;}}''',
'android/view/accessibility/AccessibilityEvent.java': '''package android.view.accessibility; public class AccessibilityEvent {}''',
'android/view/accessibility/AccessibilityNodeInfo.java': '''package android.view.accessibility; import android.os.Bundle;
public class AccessibilityNodeInfo {
 public static final int FOCUS_INPUT=1,ACTION_SET_TEXT=2,ACTION_SET_SELECTION=3,ACTION_PASTE=4; public static final String ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE="text",ACTION_ARGUMENT_SELECTION_START_INT="start",ACTION_ARGUMENT_SELECTION_END_INT="end";
 public int id=7,window=3,start=7,end=7,cursor=-9; public String text="Start: ",hint=null,label=null; public boolean live=true,password=false,focused=true,editable=true,showingHint=false,multi=false,accepts=true; public AccessibilityNodeInfo child; public int writes;
 public boolean refresh(){return live;} public boolean isPassword(){return password;} public boolean isFocused(){return focused;} public boolean isEditable(){return editable;} public int getWindowId(){return window;} public CharSequence getText(){return text;} public CharSequence getHintText(){return hint;} public CharSequence getContentDescription(){return label;} public boolean isShowingHintText(){return showingHint;} public boolean isMultiLine(){return multi;} public int getTextSelectionStart(){return start;} public int getTextSelectionEnd(){return end;}
 public AccessibilityNodeInfo findFocus(int kind){return child;}
 public boolean performAction(int action,Bundle args){if(action==ACTION_SET_SELECTION){cursor=args.ints.get("start");return true;} if(!accepts)return false; writes++;text=args.text.toString();return true;}
}''',
'app/scrivo/TargetCheck.java': '''package app.scrivo;
import android.accessibilityservice.AccessibilityService; import android.view.accessibility.*;
public class TargetCheck {
 static AccessibilityNodeInfo old,field,root; static TextTargetService.Target target; static int checks;
 static void setup(){FieldTools.rich=false;FieldTools.web=false;FieldTools.pasteWorks=true;FieldTools.pastes=0;FieldTools.pasted=null;AccessibilityService.windows.clear();AccessibilityService.anywhere=null;TextTargetService.current=new TextTargetService();root=new AccessibilityNodeInfo();old=new AccessibilityNodeInfo();root.child=old;AccessibilityService.root=root;target=TextTargetService.capture();old.live=false;field=new AccessibilityNodeInfo();root.child=field;TextTargetService.current=new TextTargetService();}
 static void require(boolean ok,String message){if(!ok)throw new AssertionError(message+" (text: "+(field==null?null:field.text)+", note: "+FieldTools.last+")");checks++;}
 static void refused(String name){require(!target.insert("Hello"),name+" must not insert");require(field.writes==0&&FieldTools.pastes==0,name+" must leave the field alone");}
 static void typed(String name,String expected){require(target.insert("Hello"),name+" must insert");require(field.text.equals(expected)&&old.writes==0&&FieldTools.pastes==0,name+" must give "+expected);}
 public static void main(String[] args){
  setup();typed("The same text box after the typing service reconnects","Start: Hello");
  setup();field.id=8;typed("A different text box selected at the end","Start: Hello");
  setup();field.text="Edited ";typed("A text box whose words changed while speaking","Edited Hello");
  setup();field.start=0;field.end=0;typed("A cursor moved to the front","Hello Start: ");require(field.cursor==6,"The cursor must rest after the new words, not at the end");
  setup();field.start=-1;field.end=-1;typed("A text box that reports no cursor","Start: Hello");
  setup();field.text="Type a message";field.showingHint=true;field.start=-1;field.end=-1;typed("An empty text box showing its grey hint","Hello");
  setup();field.text="Search";field.hint="Search";field.start=0;field.end=0;typed("An empty text box that reports its hint as its words","Hello");
  setup();field.text="";field.start=0;field.end=0;typed("An empty text box","Hello");
  setup();field.text="Message";field.start=-1;field.end=-1;typed("A messaging box that reports its hint word without calling it a hint","Hello");
  setup();field.text="Message";field.start=7;field.end=7;typed("A hint word reported with the cursor after it","Hello");
  setup();field.text="Send message";field.start=0;field.end=0;typed("An empty message box that reports Send message","Hello");
  setup();field.text="RCS message";field.start=-1;field.end=-1;typed("An RCS messaging app's empty box","Hello");
  setup();field.text="Type a message…";field.start=0;field.end=0;typed("A hint ending in an ellipsis","Hello");
  setup();field.text="Search chats";field.hint="search chats";field.start=-1;field.end=-1;typed("A hint reported with no cursor","Hello");
  setup();field.text="Reply to Sam";field.label="Reply to Sam";field.start=-1;field.end=-1;typed("A box that reports its label as its words","Hello");
  setup();field.text="Message me later";field.start=16;field.end=16;typed("Real words that start with Message stay",  "Message me later Hello");
  setup();field.text="Thanks for the message";field.start=22;field.end=22;typed("Real words that end with message stay","Thanks for the message Hello");
  setup();field.text="one two";field.start=3;field.end=3;typed("A cursor in the middle of words","one Hello two");
  setup();field.text="keep this";field.start=5;field.end=9;typed("Selected words","keep Hello");
  setup();field.password=true;refused("A password box");
  setup();field.focused=false;refused("A view that lost the cursor");
  setup();field.editable=false;refused("A view that cannot be typed in");
  setup();TextTargetService.current=null;refused("A disconnected typing service");
  setup();root.child=null;old.live=false;refused("No text box selected");
  setup();field.accepts=false;require(target.insert("Hello")&&FieldTools.pastes==1&&"Hello".equals(FieldTools.pasted),"A text box that refuses typing must get the words pasted");
  setup();field.accepts=false;FieldTools.pasteWorks=false;require(!target.insert("Hello"),"Refused typing and pasting falls back to the clipboard");
  setup();field.multi=true;FieldTools.web=true;require(target.insert("Hello")&&FieldTools.pastes==1&&field.writes==0,"Web-page writing with words in it is pasted into, never replaced");
  setup();FieldTools.rich=true;require(target.insert("Hello")&&FieldTools.pastes==1&&field.writes==0,"Formatted writing is pasted into, never replaced");
  setup();field.multi=true;FieldTools.web=true;FieldTools.pasteWorks=false;typed("Web-page writing that refuses pasting","Start: Hello");
  setup();AccessibilityService.windows.add(new AccessibilityWindowInfo(null,true));AccessibilityService.windows.add(new AccessibilityWindowInfo(root,true));typed("A focused system window with nothing in it beside the app","Start: Hello");
  setup();AccessibilityService.windows.add(new AccessibilityWindowInfo(null,true));AccessibilityService.root=null;AccessibilityService.anywhere=field;typed("A text box found only by the system-wide lookup","Start: Hello");
  setup();AccessibilityService.windows.add(new AccessibilityWindowInfo(root,true));AccessibilityService.root=new AccessibilityNodeInfo();AccessibilityService.root.window=9;target=TextTargetService.capture();require(target!=null&&target.node!=null,"Holding the control must still see the browser text box");typed("The browser text box while the control is held","Start: Hello");
  setup();AccessibilityService.windows.add(new AccessibilityWindowInfo(root,false));AccessibilityService.root=new AccessibilityNodeInfo();target=TextTargetService.capture();require(target!=null&&target.node==null,"A background window's text box must not be picked up");refused("A background window's text box");
  setup();root.child=null;target=TextTargetService.capture();require(target.node==null,"Starting without a text box is allowed");root.child=field;typed("A text box selected after speaking began","Start: Hello");
  require(TextTargetService.spaced("Hello",5,5,"world").equals(" world")&&TextTargetService.spaced("Hello ",6,6,"world").equals("world")&&TextTargetService.spaced("(",1,1,"world").equals("world")&&TextTargetService.spaced("Start\\u00a0",6,6,"world").equals("world")&&TextTargetService.spaced("Hello",5,5,", yes").equals(", yes"),"Spacing around the cursor");
  System.out.println("PASS: "+checks+" checks of where dictated words go");
 }
}'''
}

def tool(name):
    """A JDK program from $JAVA_HOME/bin, else from PATH."""
    if os.environ.get('JAVA_HOME'):
        path = Path(os.environ['JAVA_HOME']).expanduser()/'bin'/name
        if path.is_file():
            return str(path)
    found = shutil.which(name)
    if not found:
        sys.exit(f'Missing: {name} (set JAVA_HOME to a JDK 17 or put {name} on PATH)')
    return found

def main():
    javac, java = tool('javac'), tool('java')
    with tempfile.TemporaryDirectory(prefix='scrivo-target-check-') as folder:
        tmp=Path(folder)
        for name,body in SOURCES.items():
            path=tmp/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body, encoding='utf-8')
        source=tmp/'app/scrivo/TextTargetService.java'
        source.write_text((ROOT/'android/app/src/main/java/app/scrivo/TextTargetService.java').read_text(encoding='utf-8'), encoding='utf-8')
        subprocess.run([javac,'-encoding','UTF-8','-d',str(tmp),*[str(p) for p in tmp.rglob('*.java')]],check=True)
        subprocess.run([java,'-cp',str(tmp),'app.scrivo.TargetCheck'],check=True)

if __name__=='__main__':main()
