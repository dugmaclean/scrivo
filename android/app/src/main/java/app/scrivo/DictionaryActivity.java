package app.scrivo;

import android.app.Activity;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.View;
import android.view.ViewGroup;
import android.widget.*;
import org.json.JSONArray;
import org.json.JSONObject;
import java.util.ArrayList;
import java.util.Locale;

/** Searchable view of every saved phrase, replacement and shortcut. */
public class DictionaryActivity extends Activity {
    private final java.util.concurrent.ExecutorService requests=java.util.concurrent.Executors.newSingleThreadExecutor();
    private final ArrayList<String[]> entries=new ArrayList<>(), visible=new ArrayList<>();
    private EditText search;
    private TextView status;
    private TextView refresh;
    private BaseAdapter adapter;
    private boolean loading, loaded;
    private int dp(int n){return (int)(getResources().getDisplayMetrics().density*n);}
    public void onCreate(Bundle state){
        super.onCreate(state);
        LinearLayout root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);
        int side=Math.max(dp(20),(getResources().getDisplayMetrics().widthPixels-dp(600))/2);
        root.setPadding(side,dp(12),side,0);
        setContentView(root);
        Ui.header(root,"Dictionary",v->finish());
        Ui.note(root,"Names and words spelled your way. Saved on your server and shared by every device.");
        search=new EditText(this);search.setSingleLine(true);search.setHint("Search your words");search.setContentDescription("Search your words");
        search.setTextSize(17);search.setTextColor(getColor(R.color.ink));search.setHintTextColor(getColor(R.color.secondary));search.setMinHeight(dp(56));
        LinearLayout.LayoutParams sp=new LinearLayout.LayoutParams(-1,-2);sp.topMargin=dp(8);root.addView(search,sp);
        LinearLayout actions=new LinearLayout(this);root.addView(actions,new LinearLayout.LayoutParams(-1,-2));
        TextView add=Ui.button(actions,"Add a word",true,v->addWord());
        refresh=Ui.button(actions,"Import a file",false,v->startActivityForResult(new android.content.Intent(android.content.Intent.ACTION_OPEN_DOCUMENT).setType("*/*").addCategory(android.content.Intent.CATEGORY_OPENABLE),2));
        LinearLayout.LayoutParams left=new LinearLayout.LayoutParams(0,-2,1);left.topMargin=dp(8);left.setMarginEnd(dp(6));add.setLayoutParams(left);
        LinearLayout.LayoutParams right=new LinearLayout.LayoutParams(0,-2,1);right.topMargin=dp(8);right.setMarginStart(dp(6));refresh.setLayoutParams(right);
        status=Ui.note(root,"");
        ListView list=new ListView(this);list.setDivider(new android.graphics.drawable.ColorDrawable(getColor(R.color.line)));list.setDividerHeight(Math.max(1,dp(1)));
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,0,1);lp.topMargin=dp(8);root.addView(list,lp);
        adapter=new BaseAdapter(){
            public int getCount(){return visible.size();}
            public Object getItem(int position){return visible.get(position);}
            public long getItemId(int position){return position;}
            public boolean isEnabled(int position){return false;}
            public View getView(int position,View reused,ViewGroup parent){
                LinearLayout row;
                if(reused instanceof LinearLayout)row=(LinearLayout)reused;
                else{
                    row=new LinearLayout(DictionaryActivity.this);row.setOrientation(LinearLayout.VERTICAL);row.setPadding(dp(4),dp(12),dp(4),dp(12));
                    row.addView(Ui.text(DictionaryActivity.this,"",17,R.color.ink));
                    row.addView(Ui.text(DictionaryActivity.this,"",15,R.color.secondary));
                }
                String[] entry=visible.get(position);
                ((TextView)row.getChildAt(0)).setText(entry[0]);
                TextView detail=(TextView)row.getChildAt(1);detail.setText(entry[1]);detail.setVisibility(entry[1].isEmpty()?View.GONE:View.VISIBLE);
                return row;
            }
        };
        list.setAdapter(adapter);
        search.addTextChangedListener(new TextWatcher(){
            public void beforeTextChanged(CharSequence s,int start,int count,int after){}
            public void onTextChanged(CharSequence s,int start,int before,int count){filter();}
            public void afterTextChanged(Editable text){}
        });
        load();
    }
    private void addWord(){
        EditText word=new EditText(this);word.setSingleLine(true);word.setHint("Word or name");word.setMinHeight(dp(56));
        android.widget.FrameLayout holder=new android.widget.FrameLayout(this);holder.setPadding(dp(20),dp(8),dp(20),0);holder.addView(word);
        new android.app.AlertDialog.Builder(this).setTitle("Add a word").setView(holder).setNegativeButton("Cancel",null).setPositiveButton("Save",(d,w)->{
            final String value=word.getText().toString().trim();if(value.isEmpty())return;
            requests.execute(()->{try{ServiceClient.request(getSharedPreferences("dictation",MODE_PRIVATE),"/v1/dictionary/import",new JSONObject().put("data",new JSONArray().put(value).toString()).put("format","json"));runOnUiThread(()->{if(!isDestroyed())load();});}catch(Exception e){runOnUiThread(()->{if(!isDestroyed())status.setText("Could not save. Your earlier words are safe; try again.");});}});
        }).show();
    }
    private void filter(){
        String query=search.getText().toString().trim().toLowerCase(Locale.ROOT);
        visible.clear();
        for(String[] row:entries)if((row[0]+"\n"+row[1]).toLowerCase(Locale.ROOT).contains(query))visible.add(row);
        adapter.notifyDataSetChanged();
        if(loaded&&!loading){
            if(entries.isEmpty())status.setText("No words saved yet. Add one, or import a file.");
            else if(visible.isEmpty())status.setText("Nothing matches. Clear the search to see all your words.");
            else status.setText(query.isEmpty()?entries.size()+" saved":visible.size()+" of "+entries.size()+" match");
        }
    }
    private void load(){
        if(loading)return;loading=true;status.setText("Loading your words\u2026");
        requests.execute(()->{
            ArrayList<String[]> loadedEntries=new ArrayList<>();
            try{
                JSONObject reply=ServiceClient.request(getSharedPreferences("dictation",MODE_PRIVATE),"/v1/dictionary",null);
                JSONArray structured=reply.optJSONArray("entries");
                java.util.HashSet<String> represented=new java.util.HashSet<>();
                if(structured!=null)for(int i=0;i<structured.length();i++){
                    JSONObject entry=structured.getJSONObject(i);String phrase=entry.getString("phrase");
                    String replacement=entry.isNull("replacement")?"":entry.optString("replacement","");
                    String detail=replacement.isEmpty()?"":"Writes: "+replacement;
                    if(entry.optBoolean("is_snippet"))detail="Shortcut"+(detail.isEmpty()?"":"\n"+detail);
                    loadedEntries.add(new String[]{phrase,detail});represented.add(phrase.toLowerCase(Locale.ROOT));
                    if(!replacement.isEmpty())represented.add(replacement.toLowerCase(Locale.ROOT));
                }
                JSONArray words=reply.getJSONArray("words");
                for(int i=0;i<words.length();i++){
                    String word=words.getString(i);
                    if(represented.add(word.toLowerCase(Locale.ROOT)))loadedEntries.add(new String[]{word,""});
                }
                loadedEntries.sort((a,b)->a[0].compareToIgnoreCase(b[0]));
                runOnUiThread(()->{if(isDestroyed())return;loading=false;loaded=true;entries.clear();entries.addAll(loadedEntries);filter();});
            }catch(Exception error){
                runOnUiThread(()->{if(isDestroyed())return;loading=false;status.setText("Couldn't load your words. Your server didn't answer; nothing was lost. Leave this page and come back to try again.");});
            }
        });
    }
    /** Add the words in the picked file; earlier words are always kept. */
    protected void onActivityResult(int request,int result,android.content.Intent data){
        super.onActivityResult(request,result,data);
        if(request!=2||result!=RESULT_OK||data==null||data.getData()==null)return;
        final android.net.Uri file=data.getData();status.setText("Importing\u2026");
        requests.execute(()->{
            String message;
            try{
                String value;try(java.io.InputStream in=getContentResolver().openInputStream(file)){value=ServiceClient.readText(in);}
                String trimmed=value.trim(),format=trimmed.startsWith("[")||trimmed.startsWith("{")?"json":"text";
                String first=trimmed.split("\\r?\\n",2)[0];
                if(first.equals("word")||first.equals("term")||first.startsWith("word,")||first.startsWith("term,"))format="csv";
                ServiceClient.request(getSharedPreferences("dictation",MODE_PRIVATE),"/v1/dictionary/import",new JSONObject().put("data",value).put("format",format));
                message=null;
            }catch(Exception error){message="Couldn't import that file. Your earlier words are safe.";}
            final String problem=message;
            runOnUiThread(()->{if(isDestroyed())return;if(problem==null)load();else status.setText(problem);});
        });
    }
    protected void onDestroy(){requests.shutdownNow();super.onDestroy();}
}
