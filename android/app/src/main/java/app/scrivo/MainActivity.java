package app.scrivo;

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.text.TextUtils;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;
import android.widget.ImageButton;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Settings. Dictation itself happens in the floating bubble over other apps, so this
 * screen is only for choices: which server this phone talks to, which models write the
 * words (a primary and two backups), the bubble, saved words and saved speech. Every
 * control shows its result on screen, so nothing here changes silently.
 */
public class MainActivity extends Activity {
    private static final int SETTINGS=0,PICKER=1,MICROPHONE=2;
    private static final String[] SLOTS={"Primary","Fallback 1","Fallback 2"};
    private final java.util.concurrent.ExecutorService requests=java.util.concurrent.Executors.newSingleThreadExecutor();
    private final Handler main=new Handler(Looper.getMainLooper());
    private SharedPreferences prefs;
    private int screen=SETTINGS,slot;
    // What the server last told this screen.
    private JSONArray models;
    private final String[] picks=new String[3];
    private boolean loading,updating,unreachable,rejected,testing,saving;
    // The server form. Typed text is kept here because the screen is redrawn often.
    private boolean editingServer;
    private String addressDraft,tokenDraft,serverProblem;
    private int formFocus,serverVersion;
    // Brought into view once the screen is drawn, such as the form just opened by Change.
    private boolean revealForm;
    private View reveal;
    private int savedWords=-1;
    private String testResult,pickerProblem,copied,recovering,lastWriting;
    // The big microphone page.
    private TextView micStatus,micLast;
    private final SharedPreferences.OnSharedPreferenceChangeListener changed=(p,k)->{
        if(screen==MICROPHONE)refreshMicrophone();
        else if(screen==SETTINGS&&("last_text".equals(k)||"state".equals(k)))show();
    };

    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        prefs=getSharedPreferences("dictation",MODE_PRIVATE);
        BubbleService.forgetStaleState(this);
        prefs.registerOnSharedPreferenceChangeListener(changed);
        BubbleService.prepare(this);
        show();load();
    }
    // Coming back from the phone's own settings: show what is switched on now.
    @Override protected void onResume(){super.onResume();TextTargetService.refreshBubble();show();}
    @Override public void onBackPressed(){if(screen!=SETTINGS){screen=SETTINGS;pickerProblem=null;show();}else super.onBackPressed();}
    @Override protected void onDestroy(){
        main.removeCallbacksAndMessages(null);
        prefs.unregisterOnSharedPreferenceChangeListener(changed);requests.shutdownNow();super.onDestroy();
    }
    @Override public void onRequestPermissionsResult(int code,String[] names,int[] results){show();BubbleService.prepare(this);}

    private boolean connected(){return BubbleService.connected(this);}
    private boolean microphoneAllowed(){return checkSelfPermission(Manifest.permission.RECORD_AUDIO)==PackageManager.PERMISSION_GRANTED;}
    private boolean typingOn(){
        if(TextTargetService.current!=null)return true;
        String on=Settings.Secure.getString(getContentResolver(),Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        return on!=null&&on.contains(getPackageName()+"/");
    }
    private JSONObject model(String key){
        if(models==null||key==null)return null;
        for(int i=0;i<models.length();i++){JSONObject m=models.optJSONObject(i);if(m!=null&&key.equals(m.optString("key")))return m;}
        return null;
    }
    private String where(JSONObject m){
        return m.optBoolean("cloud")?"Cloud. Your words go to "+m.optString("company","a cloud service")+".":"Stays on your server. Fastest.";
    }

    /** Draw the current screen again, keeping the place it was scrolled to. */
    private void show(){
        if(isDestroyed())return;
        View old=findViewById(android.R.id.content);
        int y=0;
        if(old instanceof android.view.ViewGroup&&((android.view.ViewGroup)old).getChildCount()>0){
            View first=((android.view.ViewGroup)old).getChildAt(0);
            if(first instanceof ScrollView&&Integer.valueOf(screen).equals(first.getTag()))y=first.getScrollY();
        }
        micStatus=null;micLast=null;
        LinearLayout page=Ui.page(this);
        final ScrollView scroll=(ScrollView)((android.view.ViewGroup)findViewById(android.R.id.content)).getChildAt(0);
        scroll.setTag(screen);
        if(screen==PICKER)picker(page);else if(screen==MICROPHONE)microphone(page);else settings(page);
        final int keep=y;
        if(keep>0)scroll.post(()->scroll.scrollTo(0,keep));
        final View wanted=reveal;reveal=null;
        if(wanted!=null)scroll.post(()->wanted.requestRectangleOnScreen(new android.graphics.Rect(0,0,wanted.getWidth(),wanted.getHeight()),false));
    }

    // ---- Settings -------------------------------------------------------------------

    private void settings(LinearLayout page){
        Ui.header(page,"Scrivo",null);
        status(page);
        // Until this phone knows its server, connecting is the first thing on the screen.
        if(!connected())server(page);

        Ui.section(page,"Writing models","Your voice is always turned into words on your server. Then a writing model tidies them. If the first one can't answer, the next one takes over.");
        if(models!=null){
            LinearLayout card=Ui.card(page);
            for(int i=0;i<SLOTS.length;i++){
                final int which=i;JSONObject m=model(picks[i]);
                boolean fine=m!=null&&m.optBoolean("available");
                Ui.Row row=Ui.row(card,SLOTS[i],m==null?(picks[i]==null?"Not chosen":"No longer offered"):m.optString("label"),
                    m==null?(picks[i]==null?"Tap to pick a backup.":"Pick another model."):fine?where(m):"Can't be used right now. Pick another.",
                    "\u203A",v->{slot=which;screen=PICKER;pickerProblem=null;show();});
                if(!fine&&picks[i]!=null)row.detail.setTextColor(getColor(R.color.warn));
            }
            if(lastWriting!=null)Ui.note(page,lastWriting);
            final TextView test=Ui.button(page,testing?"Testing…":"Test my writing models",true,v->test());
            if(testing)Ui.busy(test,true,"Testing…");
            if(testResult!=null)Ui.note(page,testResult);
        }else if(loading){
            Ui.row(Ui.card(page),"Loading your models…",null,null,null);
        }else if(!connected()){
            Ui.note(page,"Your model choices show here once this phone is connected to your server.");
        }else if(rejected){
            Ui.note(page,"Your server didn't accept the access token, so your model choices can't be shown.").setTextColor(getColor(R.color.warn));
        }else if(updating){
            Ui.note(page,"Your server doesn't offer model choices yet. Update the server to choose models here. Dictation keeps working meanwhile.");
            Ui.button(page,"Check again",false,v->load());
        }else{
            Ui.note(page,"Can't reach your server right now, so your model choices can't be shown.").setTextColor(getColor(R.color.warn));
            Ui.button(page,"Try again",false,v->load());
        }

        Ui.section(page,"Floating bubble",null);
        LinearLayout bubble=Ui.card(page);
        Ui.Row showBubble=Ui.row(bubble,"Show the bubble","A small teal button at the edge of your screen. Tap it and talk. Long press it to move it.",null,null);
        final Switch on=new Switch(this);on.setChecked(prefs.getBoolean("bubble",true));on.setContentDescription("Show the bubble");
        on.setMinimumHeight(Ui.dp(this,48));on.setMinimumWidth(Ui.dp(this,56));showBubble.view.addView(on);
        showBubble.view.setOnClickListener(v->on.toggle());
        on.setOnCheckedChangeListener((v,checked)->{prefs.edit().putBoolean("bubble",checked).apply();TextTargetService.refreshBubble();show();});
        if(typingOn())Ui.row(bubble,"Typing into other apps","On",null,null);
        else Ui.row(bubble,"Typing into other apps","Off, so the bubble can't show. Tap here, choose Scrivo, and switch it on.","›",
            v->startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))).detail.setTextColor(getColor(R.color.warn));
        if(microphoneAllowed())Ui.row(bubble,"Microphone","Allowed",null,null);
        else Ui.row(bubble,"Microphone","Not allowed yet. Tap to allow.","›",
            v->requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO,Manifest.permission.POST_NOTIFICATIONS},1)).detail.setTextColor(getColor(R.color.warn));

        Ui.section(page,"Dictionary",null);
        Ui.row(Ui.card(page),"Saved words and corrections",(savedWords>=0?savedWords+" saved. ":"")+"Saved on your server and shared by every device. Search, add or import here.","›",
            v->startActivity(new Intent(this,DictionaryActivity.class)));

        Ui.section(page,"Saved speech",null);
        LinearLayout speech=Ui.card(page);
        final String last=prefs.getString("last_text","");
        Ui.Row lastRow=Ui.row(speech,"Last dictation",last.isEmpty()?"Nothing dictated yet.":last,last.isEmpty()?null:copied!=null?copied:"Copy",last.isEmpty()?null:v->copy(last));
        lastRow.detail.setMaxLines(3);lastRow.detail.setEllipsize(TextUtils.TruncateAt.END);
        lastRow.side.setTextColor(getColor(R.color.teal));lastRow.side.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        final java.io.File[] waiting=waitingRecordings();
        if(recovering!=null)Ui.row(speech,"Recordings to recover",recovering,null,null);
        else if(waiting.length==0)Ui.row(speech,"Recordings to recover","None. Every recording was turned into words.",null,null);
        else Ui.row(speech,"Recordings to recover",waiting.length+(waiting.length==1?" recording is":" recordings are")+" kept on this phone. Tap to turn one into words.","›",v->recover(waiting));

        if(connected())server(page);

        Ui.section(page,"More",null);
        Ui.row(Ui.card(page),"Dictate inside this app","A big microphone, for when the bubble isn't handy.","›",v->{screen=MICROPHONE;show();});
    }

    /**
     * The server this phone talks to. Before it is set this is an open form at the top of the
     * screen; after that it is a short summary lower down, with the address shown, the token
     * never shown, and a Change button that opens the same form again.
     */
    private void server(LinearLayout page){
        if(!connected()){
            Ui.section(page,"Connect to your server","Type the address and access token of your Scrivo server. You only need to do this once on this phone.");
            serverForm(page,false);
            return;
        }
        Ui.section(page,"Your server",null);
        LinearLayout card=Ui.card(page);
        String link=loading?"Checking…":rejected?"Your server didn't accept the access token. Enter it again below.":unreachable?"Not reachable right now. Tap to try again.":"Connected";
        boolean fine=loading||"Connected".equals(link);
        Ui.Row state=Ui.row(card,"Connection",link,fine||rejected?null:"\u203A",fine||rejected?null:v->load());
        if(!fine)state.detail.setTextColor(getColor(R.color.warn));
        boolean open=editingServer||rejected;
        Ui.Row address=Ui.row(card,"Server address",prefs.getString("endpoint",""),open?null:"Change",open?null:v->{editingServer=true;revealForm=true;show();});
        address.side.setTextColor(getColor(R.color.teal));address.side.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        Ui.row(card,"Access token","Saved on this phone. It is never shown.",null,null);
        if(open)serverForm(page,!rejected);
    }
    /** The address and token boxes with their Save button; what is typed survives a redraw. */
    private void serverForm(LinearLayout page,boolean cancellable){
        final boolean change=connected();
        LinearLayout form=new LinearLayout(this);form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(Ui.dp(this,16),Ui.dp(this,4),Ui.dp(this,16),Ui.dp(this,16));
        LinearLayout card=Ui.card(page);card.addView(form,new LinearLayout.LayoutParams(-1,-2));
        if(revealForm){revealForm=false;reveal=card;}
        final EditText address=Ui.field(form,"Server address","https://dictation.example.com",false);
        final EditText token=Ui.field(form,"Access token",change?"Leave empty to keep the saved token":"At least 32 characters",true);
        address.setText(addressDraft!=null?addressDraft:prefs.getString("endpoint",""));
        if(tokenDraft!=null)token.setText(tokenDraft);
        Ui.typed(address,t->addressDraft=t);Ui.typed(token,t->tokenDraft=t);
        address.setOnFocusChangeListener((v,has)->{if(has)formFocus=1;});
        token.setOnFocusChangeListener((v,has)->{if(has)formFocus=2;});
        address.setImeOptions(EditorInfo.IME_ACTION_NEXT);token.setImeOptions(EditorInfo.IME_ACTION_DONE);
        token.setOnEditorActionListener((v,action,event)->{
            if(action!=EditorInfo.IME_ACTION_DONE)return false;
            saveServer(address.getText().toString(),token.getText().toString());return true;
        });
        if(serverProblem!=null)Ui.note(form,serverProblem).setTextColor(getColor(R.color.warn));
        Ui.button(form,"Save",true,v->saveServer(address.getText().toString(),token.getText().toString()));
        if(cancellable)Ui.button(form,"Cancel",false,v->{closeServerForm();show();});
        EditText focus=formFocus==1?address:formFocus==2?token:null;
        if(focus!=null){focus.requestFocus();focus.setSelection(focus.length());}
    }
    /** Check what was typed, then save it where the rest of the app reads it and connect. */
    private void saveServer(String typedAddress,String typedToken){
        String typed=typedAddress.trim(),address=ServiceClient.serverAddress(typed),token=typedToken.trim();
        boolean keepToken=token.isEmpty()&&connected();
        if(typed.isEmpty())serverProblem="Type your server's address first.";
        else if(typed.regionMatches(true,0,"http://",0,7))serverProblem="The address must start with https://. Scrivo only talks to its server over a secure connection.";
        else if(address==null)serverProblem="That address can't be used. Type your server's https:// address, without a user name, password or anything after a ? or #.";
        else if(!keepToken&&token.isEmpty())serverProblem="Paste the access token from your server.";
        else if(!keepToken&&token.matches(".*\\s.*"))serverProblem="The access token can't contain spaces.";
        else if(!keepToken&&!ServiceClient.acceptableToken(token))serverProblem="That access token is too short. It has at least 32 characters.";
        else serverProblem=null;
        if(serverProblem!=null){show();return;}
        SharedPreferences.Editor edit=prefs.edit().putString("endpoint",address);
        if(!keepToken)edit.putString("token",token);
        edit.apply();
        // A new server has its own models and words; forget what the old one said.
        serverVersion++;closeServerForm();
        models=null;java.util.Arrays.fill(picks,null);savedWords=-1;lastWriting=null;testResult=null;
        updating=false;unreachable=false;rejected=false;
        TextTargetService.refreshBubble();load();
    }
    private void closeServerForm(){
        editingServer=false;addressDraft=null;tokenDraft=null;serverProblem=null;formFocus=0;
        View focus=getCurrentFocus();
        if(focus!=null)getSystemService(InputMethodManager.class).hideSoftInputFromWindow(focus.getWindowToken(),0);
    }

    /** One plain line at the top: ready, or the one thing that is in the way. */
    private void status(LinearLayout page){
        String words;boolean good=false;
        if(!connected())words="To start, type your server address and access token below.";
        else if(rejected)words="Your server didn't accept the access token. Enter it again under Your server below.";
        else if(!typingOn())words="Typing into other apps is off, so the bubble can't show. Turn it on below.";
        else if(!microphoneAllowed())words="The microphone isn't allowed yet. Allow it below.";
        else if(!prefs.getBoolean("bubble",true))words="The bubble is switched off. Turn it on below to dictate in other apps.";
        else if(unreachable)words="Can't reach your server right now. Recordings are kept on this phone until it answers.";
        else{words="Ready. Tap the bubble in any app and talk.";good=true;}
        LinearLayout line=new LinearLayout(this);line.setGravity(Gravity.CENTER_VERTICAL);
        View dot=new View(this);GradientDrawable shape=new GradientDrawable();shape.setShape(GradientDrawable.OVAL);
        shape.setColor(getColor(good?R.color.teal:R.color.warn));dot.setBackground(shape);
        LinearLayout.LayoutParams dp=new LinearLayout.LayoutParams(Ui.dp(this,10),Ui.dp(this,10));dp.setMarginEnd(Ui.dp(this,10));line.addView(dot,dp);
        TextView text=Ui.text(this,words,16,good?R.color.ink:R.color.warn);text.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        line.addView(text,new LinearLayout.LayoutParams(0,-2,1));
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.topMargin=Ui.dp(this,8);page.addView(line,lp);
    }

    /** Ask the server for its models, the three picks and how many words are saved. */
    private void load(){
        if(loading)return;
        if(!connected()){show();return;}
        loading=true;show();
        final int version=serverVersion;
        requests.execute(()->{
            JSONObject answer=null;boolean late=false,down=false,refused=false;int words=-1;
            try{answer=ServiceClient.request(prefs,"/v1/models",null);}
            catch(ServiceClient.Refused no){if(no.code==404)late=true;else if(no.code==401||no.code==403)refused=true;else down=true;}
            catch(Exception error){down=true;}
            if(!down&&!refused){
                try{
                    JSONObject saved=ServiceClient.request(prefs,"/v1/dictionary",null);
                    JSONArray entries=saved.optJSONArray("entries");
                    words=entries!=null&&entries.length()>0?entries.length():saved.getJSONArray("words").length();
                }catch(Exception error){/* the count is a nicety */}
            }
            final JSONObject got=answer;final boolean isLate=late,isDown=down,isRefused=refused;final int count=words;
            runOnUiThread(()->{
                loading=false;
                if(isDestroyed())return;
                // The server was changed while this answer was on its way: ask the new one.
                if(version!=serverVersion){load();return;}
                updating=isLate;unreachable=isDown;rejected=isRefused;
                if(count>=0)savedWords=count;
                if(got!=null)accept(got);
                show();
            });
        });
    }
    private void accept(JSONObject answer){
        JSONArray slots=answer.optJSONArray("slots"),list=answer.optJSONArray("models");
        if(slots==null||list==null||slots.length()!=3)return;
        models=list;
        JSONObject last=answer.optJSONObject("last");lastWriting=null;
        if(last!=null){
            JSONArray failed=last.optJSONArray("failed");StringBuilder missed=new StringBuilder();
            for(int i=0;failed!=null&&i<failed.length();i++)missed.append(i>0?", ":"").append(failed.optString(i));
            String writer=last.isNull("writer")?null:last.optString("writer");
            if("not-needed".equals(writer))lastWriting="Your last dictation needed no tidying, so no model was used.";
            else if(writer==null)lastWriting="Your last dictation: none of your models answered"+(missed.length()>0?" ("+missed+")":"")+", so you got the words as heard.";
            else if(!last.isNull("writer_label"))lastWriting="Your last dictation was written by "+last.optString("writer_label")+(missed.length()>0?", because "+missed+" didn't answer.":".");
        }
        for(int i=0;i<3;i++){JSONObject s=slots.optJSONObject(i);picks[i]=s==null||s.isNull("key")?null:s.optString("key");}
    }

    private void test(){
        if(testing)return;
        testing=true;testResult=null;show();
        requests.execute(()->{
            String result;
            try{
                // Three cloud models in a row can each take a while before the next is tried.
                JSONObject answer=ServiceClient.request(prefs,"/v1/models/test",new JSONObject(),130000);
                JSONArray failed=answer.optJSONArray("failed");
                StringBuilder missed=new StringBuilder();
                for(int i=0;failed!=null&&i<failed.length();i++)missed.append(i>0?", ":"").append(failed.optString(i));
                if(answer.isNull("writer"))result="None of your models answered ("+missed+"). You would still get your words, just not tidied.";
                else result="✓ "+answer.optString("writer_label")+" wrote: “"+answer.optString("text")+"”"
                    +(missed.length()>0?"\n"+missed+" didn't answer, so the next one took over.":"");
            }catch(Exception error){result="Couldn't run the test. Your server didn't answer.";}
            final String words=result;
            runOnUiThread(()->{testing=false;testResult=words;show();});
        });
    }

    private void copy(String words){
        getSystemService(ClipboardManager.class).setPrimaryClip(ClipData.newPlainText("Dictation",words));
        copied="Copied ✓";show();
        main.postDelayed(()->{copied=null;if(screen==SETTINGS)show();},2500);
    }
    private java.io.File[] waitingRecordings(){
        java.io.File[] files=new java.io.File(getFilesDir(),"recordings").listFiles((dir,name)->name.matches("[a-f0-9-]{36}\\.wav"));
        if(files==null)return new java.io.File[0];
        java.util.Arrays.sort(files,(a,b)->Long.compare(b.lastModified(),a.lastModified()));
        return files;
    }
    private void recover(java.io.File[] files){
        String[] labels=new String[Math.min(20,files.length)];
        java.text.DateFormat dates=new java.text.SimpleDateFormat("MMM d, h:mm a",java.util.Locale.getDefault());
        for(int i=0;i<labels.length;i++)labels[i]="Recording from "+dates.format(new java.util.Date(files[i].lastModified()));
        new AlertDialog.Builder(this).setTitle("Turn a recording into words").setItems(labels,(d,i)->{
            if(!microphoneAllowed()){requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO},1);return;}
            startForegroundService(new Intent(this,BubbleService.class).putExtra("retry_recording",files[i].getName()));
            recovering="Working on it. The words will be copied, ready for you to paste, and will show under Last dictation.";show();
            main.postDelayed(()->{recovering=null;if(screen==SETTINGS)show();},8000);
        }).setNegativeButton("Cancel",null).show();
    }

    // ---- Choosing a model -------------------------------------------------------------

    private void picker(LinearLayout page){
        Ui.header(page,SLOTS[slot],v->onBackPressed());
        Ui.note(page,slot==0?"This model tidies your words first.":slot==1?"Used only when your primary can't answer.":"Used only when your primary and first backup can't answer.");
        if(saving)Ui.note(page,"Saving…");
        if(pickerProblem!=null)Ui.note(page,pickerProblem).setTextColor(getColor(R.color.warn));
        if(slot>0){
            Ui.Row none=Ui.row(Ui.card(page),"No backup here",null,picks[slot]==null?"✓":null,v->choose(null));
            if(picks[slot]==null)none.side.setContentDescription("Selected");
        }
        String group=null;LinearLayout card=null;
        for(int i=0;models!=null&&i<models.length();i++){
            final JSONObject m=models.optJSONObject(i);if(m==null)continue;
            if(!m.optString("group").equals(group)){group=m.optString("group");Ui.section(page,group,null);card=Ui.card(page);}
            final String key=m.optString("key");
            int elsewhere=-1;for(int s=0;s<3;s++)if(s!=slot&&key.equals(picks[s]))elsewhere=s;
            boolean current=key.equals(picks[slot]),usable=m.optBoolean("available");
            // Taking the primary into a backup spot would leave no primary unless there is one to swap back.
            boolean stuck=elsewhere==0&&picks[slot]==null;
            String detail=!usable?m.optString("why_not","Can't be used right now")
                :stuck?"Already your Primary."
                :where(m)+(elsewhere>=0?" Now your "+SLOTS[elsewhere]+"; picking it here swaps the two.":"");
            Ui.Row row=Ui.row(card,m.optString("label"),detail,current?"✓":null,usable&&!stuck&&!current?v->choose(m):null);
            if(current){row.side.setTextColor(getColor(R.color.teal));row.side.setContentDescription("Selected");row.title.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));}
            if(!usable||stuck)row.enabled(false);
        }
    }
    private void choose(JSONObject m){
        if(saving)return;
        final String key=m==null?null:m.optString("key");
        final String[] next=picks.clone();
        for(int s=0;s<3;s++)if(s!=slot&&key!=null&&key.equals(next[s]))next[s]=picks[slot];
        next[slot]=key;
        if(m!=null&&m.optBoolean("cloud")){
            // Every time a cloud model is picked, the notice says where the words will go.
            new AlertDialog.Builder(this).setTitle("Use "+m.optString("label")+"?")
                .setMessage(m.optString("notice")+"\n\nA model on your own server keeps everything there.")
                .setNegativeButton("Cancel",null).setPositiveButton("Use it",(d,w)->save(next)).show();
        }else save(next);
    }
    private void save(String[] next){
        saving=true;pickerProblem=null;show();
        requests.execute(()->{
            JSONObject answer=null;
            try{
                JSONArray chosen=new JSONArray();boolean cloud=false;
                for(String key:next){chosen.put(key==null?JSONObject.NULL:key);JSONObject m=model(key);cloud|=m!=null&&m.optBoolean("cloud");}
                JSONObject body=new JSONObject().put("chosen",chosen);
                if(cloud)body.put("cloud_notice_shown",true);
                answer=ServiceClient.request(prefs,"/v1/models",body);
            }catch(Exception error){/* reported below */}
            final JSONObject got=answer;
            runOnUiThread(()->{
                saving=false;
                if(got==null){pickerProblem="Couldn't save that, so your earlier choice is kept. Check your connection and try again.";show();return;}
                accept(got);testResult=null;screen=SETTINGS;show();
            });
        });
    }

    // ---- The big microphone -----------------------------------------------------------

    private void microphone(LinearLayout page){
        Ui.header(page,"Dictate here",v->onBackPressed());
        micStatus=Ui.text(this,"",22,R.color.ink);micStatus.setGravity(Gravity.CENTER);
        micStatus.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);
        LinearLayout.LayoutParams sp=new LinearLayout.LayoutParams(-1,-2);sp.topMargin=Ui.dp(this,24);page.addView(micStatus,sp);
        ImageButton mic=new ImageButton(this);mic.setImageResource(R.drawable.microphone);
        mic.setContentDescription("Dictate. Tap to start, tap again to finish.");mic.setPadding(Ui.dp(this,34),Ui.dp(this,34),Ui.dp(this,34),Ui.dp(this,34));
        GradientDrawable circle=new GradientDrawable();circle.setShape(GradientDrawable.OVAL);circle.setColor(getColor(R.color.soft));mic.setBackground(circle);
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(Ui.dp(this,160),Ui.dp(this,160));lp.gravity=Gravity.CENTER;lp.topMargin=Ui.dp(this,24);lp.bottomMargin=Ui.dp(this,24);page.addView(mic,lp);
        // No press-to-talk anywhere: a tap starts and a second tap finishes, so a long dictation
        // never needs a finger held on the screen.
        mic.setOnClickListener(v->dictate("Listening".equals(prefs.getString("state",""))?"home_stop":"home_start"));
        TextView hint=Ui.text(this,"Tap to start. Tap again to finish.",16,R.color.secondary);hint.setGravity(Gravity.CENTER);page.addView(hint);
        micLast=Ui.text(this,"",20,R.color.ink);micLast.setTextIsSelectable(true);
        LinearLayout.LayoutParams tp=new LinearLayout.LayoutParams(-1,-2);tp.topMargin=Ui.dp(this,32);page.addView(micLast,tp);
        refreshMicrophone();
    }
    private void refreshMicrophone(){
        if(micStatus==null||micLast==null)return;
        micStatus.setText(connected()?prefs.getString("state","Ready"):"Enter your server address and access token in Settings first.");
        String words=prefs.getString("last_text","");micLast.setText(words.isEmpty()?"Your last dictation will appear here.":words);
    }
    private void dictate(String command){
        if(!connected()){if(micStatus!=null)micStatus.setText("Enter your server address and access token in Settings first. Nothing was recorded.");return;}
        if(!microphoneAllowed()){requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO,Manifest.permission.POST_NOTIFICATIONS},1);return;}
        startForegroundService(new Intent(this,BubbleService.class).putExtra("command",command));
    }
}
