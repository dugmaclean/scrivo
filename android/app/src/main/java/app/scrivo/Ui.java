package app.scrivo;

import android.app.Activity;
import android.content.Context;
import android.content.res.ColorStateList;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.StateListDrawable;
import android.text.Editable;
import android.text.InputType;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * The few building blocks every screen of this app is made from, so the screens
 * share one spacing scale (4, 8, 12, 16, 24), one set of type sizes (28 title,
 * 20 section, 17 row, 15 detail) and one look for cards, rows and buttons.
 */
final class Ui {
    private Ui(){}
    static int dp(Context c,float n){return Math.round(c.getResources().getDisplayMetrics().density*n);}

    /** A scrolling page with a centred column no wider than a comfortable line of text. */
    static LinearLayout page(Activity a){
        ScrollView scroll=new ScrollView(a);scroll.setFillViewport(true);scroll.setClipToPadding(false);
        FrameLayout frame=new FrameLayout(a);
        LinearLayout column=new LinearLayout(a);column.setOrientation(LinearLayout.VERTICAL);
        column.setPadding(dp(a,20),dp(a,12),dp(a,20),dp(a,32));
        int widest=dp(a,600),screen=a.getResources().getDisplayMetrics().widthPixels;
        FrameLayout.LayoutParams lp=new FrameLayout.LayoutParams(Math.min(widest,screen),ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.gravity=Gravity.CENTER_HORIZONTAL;frame.addView(column,lp);scroll.addView(frame);a.setContentView(scroll);
        return column;
    }
    static TextView text(Context c,CharSequence value,float sp,int color){
        TextView t=new TextView(c);t.setText(value);t.setTextSize(sp);t.setTextColor(c.getColor(color));t.setLineSpacing(0,1.15f);return t;
    }
    /** The page name, with a back arrow when there is somewhere to go back to. */
    static TextView header(LinearLayout page,String title,View.OnClickListener back){
        Context c=page.getContext();
        LinearLayout bar=new LinearLayout(c);bar.setGravity(Gravity.CENTER_VERTICAL);
        if(back!=null){
            TextView arrow=text(c,"←",26,R.color.ink);arrow.setGravity(Gravity.CENTER);arrow.setContentDescription("Back");
            arrow.setBackground(ripple(c,0,24));arrow.setOnClickListener(back);
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(dp(c,48),dp(c,48));lp.setMarginStart(-dp(c,12));bar.addView(arrow,lp);
        }
        TextView name=text(c,title,28,R.color.ink);name.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        name.setAccessibilityHeading(true);bar.addView(name,new LinearLayout.LayoutParams(0,ViewGroup.LayoutParams.WRAP_CONTENT,1));
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.topMargin=dp(c,8);page.addView(bar,lp);
        return name;
    }
    /** A section name with the one sentence that explains it. */
    static void section(LinearLayout page,String title,String about){
        Context c=page.getContext();
        TextView name=text(c,title,20,R.color.ink);name.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));name.setAccessibilityHeading(true);
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.topMargin=dp(c,28);page.addView(name,lp);
        if(about!=null){
            LinearLayout.LayoutParams ap=new LinearLayout.LayoutParams(-1,-2);ap.topMargin=dp(c,4);
            page.addView(text(c,about,15,R.color.secondary),ap);
        }
    }
    static TextView note(LinearLayout page,CharSequence value){
        Context c=page.getContext();TextView t=text(c,value,15,R.color.secondary);
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.topMargin=dp(c,8);page.addView(t,lp);
        t.setAccessibilityLiveRegion(View.ACCESSIBILITY_LIVE_REGION_POLITE);return t;
    }
    /** A rounded panel that holds rows. */
    static LinearLayout card(LinearLayout page){
        Context c=page.getContext();LinearLayout box=new LinearLayout(c);box.setOrientation(LinearLayout.VERTICAL);
        GradientDrawable back=new GradientDrawable();back.setColor(c.getColor(R.color.soft));back.setCornerRadius(dp(c,20));box.setBackground(back);
        box.setClipToOutline(true);
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.topMargin=dp(c,12);page.addView(box,lp);return box;
    }
    private static RippleDrawable ripple(Context c,int fill,float corner){
        GradientDrawable shape=new GradientDrawable();shape.setColor(fill);shape.setCornerRadius(dp(c,corner));
        GradientDrawable mask=new GradientDrawable();mask.setColor(0xFFFFFFFF);mask.setCornerRadius(dp(c,corner));
        return new RippleDrawable(ColorStateList.valueOf(c.getColor(R.color.press)),shape,mask);
    }
    /** One line in a card: what it is, a detail underneath, and optionally something at the right. */
    static final class Row {
        final LinearLayout view;final TextView title,detail,side;
        Row(LinearLayout v,TextView t,TextView d,TextView s){view=v;title=t;detail=d;side=s;}
        void detail(CharSequence value,int color){
            detail.setText(value);detail.setTextColor(view.getContext().getColor(color));
            detail.setVisibility(TextUtils.isEmpty(value)?View.GONE:View.VISIBLE);
        }
        void enabled(boolean on){view.setEnabled(on);view.setClickable(on);view.setAlpha(on?1f:.5f);}
    }
    static Row row(LinearLayout card,CharSequence title,CharSequence detail,CharSequence side,View.OnClickListener action){
        return row(card,null,title,detail,side,action);
    }
    /** The same line with a small label above it, such as which of the three choices this is. */
    static Row row(LinearLayout card,CharSequence over,CharSequence title,CharSequence detail,CharSequence side,View.OnClickListener action){
        Context c=card.getContext();
        if(card.getChildCount()>0){
            View line=new View(c);line.setBackgroundColor(c.getColor(R.color.line));
            LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,Math.max(1,dp(c,1)));lp.setMarginStart(dp(c,16));card.addView(line,lp);
        }
        LinearLayout row=new LinearLayout(c);row.setGravity(Gravity.CENTER_VERTICAL);row.setMinimumHeight(dp(c,64));
        row.setPadding(dp(c,16),dp(c,10),dp(c,16),dp(c,10));
        LinearLayout words=new LinearLayout(c);words.setOrientation(LinearLayout.VERTICAL);
        if(over!=null){
            TextView label=text(c,over,13,R.color.teal);label.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
            LinearLayout.LayoutParams op=new LinearLayout.LayoutParams(-1,-2);op.bottomMargin=dp(c,2);words.addView(label,op);
        }
        TextView name=text(c,title,17,R.color.ink);words.addView(name);
        TextView under=text(c,detail==null?"":detail,15,R.color.secondary);under.setVisibility(TextUtils.isEmpty(detail)?View.GONE:View.VISIBLE);
        LinearLayout.LayoutParams up=new LinearLayout.LayoutParams(-1,-2);up.topMargin=dp(c,2);words.addView(under,up);
        row.addView(words,new LinearLayout.LayoutParams(0,-2,1));
        TextView right=text(c,side==null?"":side,17,R.color.secondary);right.setPadding(dp(c,12),0,0,0);
        right.setVisibility(side==null?View.GONE:View.VISIBLE);row.addView(right);
        if(action!=null){row.setBackground(ripple(c,0,0));row.setOnClickListener(action);row.setFocusable(true);}
        card.addView(row,new LinearLayout.LayoutParams(-1,-2));
        return new Row(row,name,under,right);
    }
    /** The one filled button of a section. */
    static TextView button(LinearLayout page,String label,boolean filled,View.OnClickListener action){
        Context c=page.getContext();
        TextView b=text(c,label,17,filled?R.color.on_teal:R.color.teal);b.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        b.setGravity(Gravity.CENTER);b.setMinHeight(dp(c,52));b.setPadding(dp(c,24),dp(c,8),dp(c,24),dp(c,8));
        if(filled)b.setBackground(ripple(c,c.getColor(R.color.teal),26));
        else{
            GradientDrawable edge=new GradientDrawable();edge.setColor(0);edge.setCornerRadius(dp(c,26));edge.setStroke(dp(c,1.5f),c.getColor(R.color.teal));
            GradientDrawable mask=new GradientDrawable();mask.setColor(0xFFFFFFFF);mask.setCornerRadius(dp(c,26));
            b.setBackground(new RippleDrawable(ColorStateList.valueOf(c.getColor(R.color.press)),edge,mask));
        }
        b.setOnClickListener(action);b.setFocusable(true);
        LinearLayout.LayoutParams lp=new LinearLayout.LayoutParams(-1,-2);lp.topMargin=dp(c,12);page.addView(b,lp);return b;
    }
    /**
     * A labelled text box for inside a card: the label in the small teal type used above
     * rows, the box outlined in the divider colour and in teal while it has the cursor.
     * A secret box hides what is typed, like a password.
     */
    static EditText field(LinearLayout box,String label,String hint,boolean secret){
        Context c=box.getContext();
        TextView name=text(c,label,13,R.color.teal);name.setTypeface(Typeface.create("sans-serif-medium",Typeface.NORMAL));
        LinearLayout.LayoutParams np=new LinearLayout.LayoutParams(-1,-2);np.topMargin=dp(c,12);box.addView(name,np);
        EditText input=new EditText(c);input.setId(View.generateViewId());name.setLabelFor(input.getId());
        // The input type also makes the box a single line; setSingleLine would undo the password dots.
        input.setInputType(InputType.TYPE_CLASS_TEXT|(secret?InputType.TYPE_TEXT_VARIATION_PASSWORD:InputType.TYPE_TEXT_VARIATION_URI));
        input.setTextSize(17);input.setTextColor(c.getColor(R.color.ink));input.setHint(hint);input.setHintTextColor(c.getColor(R.color.secondary));
        input.setMinHeight(dp(c,52));input.setPadding(dp(c,14),dp(c,10),dp(c,14),dp(c,10));
        StateListDrawable edge=new StateListDrawable();
        edge.addState(new int[]{android.R.attr.state_focused},outline(c,R.color.teal,2));
        edge.addState(new int[]{},outline(c,R.color.line,1));
        input.setBackground(edge);
        LinearLayout.LayoutParams ip=new LinearLayout.LayoutParams(-1,-2);ip.topMargin=dp(c,4);box.addView(input,ip);
        return input;
    }
    private static GradientDrawable outline(Context c,int color,float width){
        GradientDrawable shape=new GradientDrawable();shape.setColor(c.getColor(R.color.surface));shape.setCornerRadius(dp(c,14));
        shape.setStroke(dp(c,width),c.getColor(color));return shape;
    }
    /** Hand every change of a text box's words to the caller. */
    static void typed(EditText input,java.util.function.Consumer<String> after){
        input.addTextChangedListener(new TextWatcher(){
            public void beforeTextChanged(CharSequence s,int start,int count,int later){}
            public void onTextChanged(CharSequence s,int start,int before,int count){}
            public void afterTextChanged(Editable text){after.accept(text.toString());}
        });
    }
    static void busy(TextView button,boolean on,String label){button.setEnabled(!on);button.setAlpha(on?.6f:1f);button.setText(label);}
}
