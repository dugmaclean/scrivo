package app.scrivo;

import android.app.KeyguardManager;
import android.content.*;
import android.graphics.*;
import android.graphics.drawable.GradientDrawable;
import android.os.*;
import android.view.*;
import android.widget.*;

/** The system-bound accessibility service owns the idle control, never a microphone service. */
final class DictationOverlay extends LinearLayout {
    private final TextTargetService owner;
    private final WindowManager windows;
    private final Handler main=new Handler(Looper.getMainLooper());
    private final ImageButton mic;
    private final Button cancel,finish;
    private final Waves waves;
    private final TextView words;
    private final ScrollView wordsBox;
    private boolean listening,processing,gesture,lifted,dragging;
    private float downX,downY;
    private int baseX,baseY;
    private android.animation.ValueAnimator settle;
    /** See-through while it only waits, so it covers less of what is underneath. */
    private static final float RESTING=.5f;
    /** Half the earlier 16: small live words, and the lines shown before they scroll. */
    private static final float LIVE_TEXT_SP=8f;
    private static final int LIVE_LINES=5;
    /** The surface, so a white ring can show that the control is picked up. */
    private final GradientDrawable surface=new GradientDrawable();
    /**
     * No press-to-talk: tapping starts a dictation and a long press moves the control.
     * A long press only picks the control up: a buzz and a white ring, never a recording.
     */
    private final Runnable lift=()->{
        if(!gesture||dragging||listening||processing)return;
        lifted=true;performHapticFeedback(HapticFeedbackConstants.LONG_PRESS);ring(true);rest();
    };
    private int dp(int n){return (int)(getResources().getDisplayMetrics().density*n);}
    private float dp(float n){return getResources().getDisplayMetrics().density*n;}
    DictationOverlay(TextTargetService service){
        super(service);owner=service;windows=service.getSystemService(WindowManager.class);
        setOrientation(VERTICAL);setGravity(Gravity.CENTER);setPadding(dp(4),dp(4),dp(4),dp(4));
        surface.setColor(0xFF006D60);surface.setCornerRadius(dp(40));setBackground(surface);
        // Live words are small on purpose: more of the sentence fits, and once it is longer
        // than the space the newest words stay in view.
        words=new TextView(service);words.setTextColor(Color.WHITE);words.setTextSize(LIVE_TEXT_SP);words.setLineSpacing(0,1.1f);
        words.setPadding(dp(14),dp(2),dp(14),dp(4));words.setContentDescription("Words heard so far");
        wordsBox=new ScrollView(service){
            @Override protected void onMeasure(int w,int h){
                int most=words.getLineHeight()*LIVE_LINES+words.getPaddingTop()+words.getPaddingBottom()+getPaddingTop();
                super.onMeasure(w,MeasureSpec.makeMeasureSpec(most,MeasureSpec.AT_MOST));
            }
        };
        // The box, not the words, keeps clear of the rounded top, so a scrolled line fades out below it.
        wordsBox.setPadding(0,dp(8),0,0);
        wordsBox.setVerticalScrollBarEnabled(false);wordsBox.setOverScrollMode(OVER_SCROLL_NEVER);
        wordsBox.setVerticalFadingEdgeEnabled(true);wordsBox.setFadingEdgeLength(dp(10));
        wordsBox.addView(words,new FrameLayout.LayoutParams(FrameLayout.LayoutParams.MATCH_PARENT,FrameLayout.LayoutParams.WRAP_CONTENT));
        wordsBox.setVisibility(GONE);addView(wordsBox,new LayoutParams(dp(216),LayoutParams.WRAP_CONTENT));
        LinearLayout controls=new LinearLayout(service);controls.setOrientation(HORIZONTAL);controls.setGravity(Gravity.CENTER);addView(controls);
        mic=new ImageButton(service);mic.setImageDrawable(new Glyph());mic.setBackgroundColor(Color.TRANSPARENT);mic.setClickable(false);mic.setContentDescription("Start dictation. Long press to move.");controls.addView(mic,new LayoutParams(dp(48),dp(48)));
        cancel=button("Cancel dictation",false);controls.addView(cancel,new LayoutParams(dp(48),dp(48)));
        waves=new Waves(service);controls.addView(waves,new LayoutParams(dp(120),dp(48)));
        finish=button("Stop dictation",true);controls.addView(finish,new LayoutParams(dp(48),dp(48)));
        cancel.setOnClickListener(v->command("home_cancel"));finish.setOnClickListener(v->command("home_stop"));
        WindowManager.LayoutParams p=new WindowManager.LayoutParams(WindowManager.LayoutParams.WRAP_CONTENT,WindowManager.LayoutParams.WRAP_CONTENT,WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE,PixelFormat.TRANSLUCENT);
        // The X, the waves and the check stay where they are: the bar grows upward when
        // words appear, so a finger already going for the check still lands on it.
        p.gravity=Gravity.RIGHT|Gravity.CENTER_VERTICAL;place(p);
        setAlpha(RESTING);windows.addView(this,p);state(owner.getSharedPreferences("dictation",Context.MODE_PRIVATE).getString("state","Ready"));
    }
    private android.content.SharedPreferences saved(){return owner.getSharedPreferences("dictation",Context.MODE_PRIVATE);}
    /**
     * Width and height the control may use, inside the status and navigation bars. When the
     * window size cannot be read (otherwise the bubble would refuse to move), the screen's
     * own size stands in, so moving is never refused on any screen size or posture.
     */
    private int[] room(){
        try{
            WindowMetrics m=windows.getCurrentWindowMetrics();
            Insets bars=m.getWindowInsets().getInsets(WindowInsets.Type.systemBars());
            int w=m.getBounds().width()-bars.left-bars.right,h=m.getBounds().height()-bars.top-bars.bottom;
            if(w>dp(56)&&h>dp(56))return new int[]{w,h};
        }catch(RuntimeException unavailable){}
        android.content.res.Resources r=getResources();android.util.DisplayMetrics screen=r.getDisplayMetrics();
        int bars=bar(r,"status_bar_height")+bar(r,"navigation_bar_height");
        return new int[]{Math.max(dp(56)+1,screen.widthPixels),Math.max(dp(56)+1,screen.heightPixels-bars)};
    }
    private static int bar(android.content.res.Resources r,String name){
        int id=r.getIdentifier(name,"dimen","android");return id==0?0:r.getDimensionPixelSize(id);
    }
    /** Picked up: a thin white ring around the control until it is put down. */
    private void ring(boolean on){surface.setStroke(on?dp(2):0,Color.WHITE);}
    /**
     * Rest against the edge it was left on, at the height it was left, on any screen size.
     * The bottom of the bar stays put so it grows upward; false when nothing changed.
     */
    private boolean place(WindowManager.LayoutParams p){
        boolean left="left".equals(saved().getString("bubble_side","right"));
        float height=Math.max(0,Math.min(1,saved().getFloat("bubble_height",.5f)));
        int side=left?Gravity.LEFT:Gravity.RIGHT,gravity=side|Gravity.CENTER_VERTICAL,y=0;
        int[] room=room();
        if(room!=null){gravity=side|Gravity.BOTTOM;y=Math.round(height*(room[1]-dp(56)));}
        if(p.gravity==gravity&&p.y==y&&p.x==0)return false;
        p.gravity=gravity;p.y=y;p.x=0;return true;
    }
    private void place(){
        if(!isAttachedToWindow()||dragging||(settle!=null&&settle.isRunning()))return;
        WindowManager.LayoutParams p=(WindowManager.LayoutParams)getLayoutParams();
        if(place(p))windows.updateViewLayout(this,p);
    }
    /** Follow the finger; the control is held by its bottom-left corner while it moves. */
    private void drag(MotionEvent e){
        int[] room=room();if(!isAttachedToWindow())return;
        WindowManager.LayoutParams p=(WindowManager.LayoutParams)getLayoutParams();
        if(!dragging){
            dragging=true;main.removeCallbacks(lift);ring(true);
            baseX=(p.gravity&Gravity.HORIZONTAL_GRAVITY_MASK)==Gravity.LEFT?p.x:room[0]-getWidth()-p.x;baseY=p.y;
            p.gravity=Gravity.LEFT|Gravity.BOTTOM;
        }
        p.x=Math.max(0,Math.min(room[0]-getWidth(),baseX+Math.round(e.getRawX()-downX)));
        p.y=Math.max(0,Math.min(room[1]-getHeight(),baseY-Math.round(e.getRawY()-downY)));
        windows.updateViewLayout(this,p);
    }
    /** Let go: slide to the nearer edge and remember the spot. */
    private void drop(){
        dragging=false;
        int[] room=room();if(!isAttachedToWindow()){place();return;}
        final WindowManager.LayoutParams p=(WindowManager.LayoutParams)getLayoutParams();
        boolean left=p.x+getWidth()/2<room[0]/2;
        int span=Math.max(1,room[1]-getHeight());
        saved().edit().putString("bubble_side",left?"left":"right").putFloat("bubble_height",Math.max(0,Math.min(1,p.y/(float)span))).apply();
        settle=android.animation.ValueAnimator.ofInt(p.x,left?0:room[0]-getWidth());settle.setDuration(180);
        settle.setInterpolator(new android.view.animation.DecelerateInterpolator());
        settle.addUpdateListener(a->{if(isAttachedToWindow()){p.x=(Integer)a.getAnimatedValue();windows.updateViewLayout(this,p);}});
        settle.addListener(new android.animation.AnimatorListenerAdapter(){@Override public void onAnimationEnd(android.animation.Animator a){settle=null;place();rest();}});
        settle.start();
    }
    /** Solid while in use or under a finger, see-through while waiting. */
    private void rest(){
        float want=listening||processing||gesture||dragging||lifted?1f:RESTING;
        if(getAlpha()!=want)animate().alpha(want).setDuration(want==1f?60:220).start();
    }
    @Override protected void onConfigurationChanged(android.content.res.Configuration c){super.onConfigurationChanged(c);place();}
    private Button button(String label,boolean done){
        Button b=new Button(owner);b.setContentDescription(label);b.setBackgroundColor(Color.TRANSPARENT);
        b.setCompoundDrawablesWithIntrinsicBounds(new Mark(done),null,null,null);b.setPadding(dp(12),dp(12),dp(12),dp(12));return b;
    }
    private final class Mark extends android.graphics.drawable.Drawable {
        final Paint ink=new Paint(Paint.ANTI_ALIAS_FLAG);final boolean done;
        Mark(boolean value){done=value;ink.setColor(Color.WHITE);ink.setStrokeWidth(dp(2));ink.setStyle(Paint.Style.STROKE);ink.setStrokeCap(Paint.Cap.ROUND);setBounds(0,0,dp(24),dp(24));}
        public int getIntrinsicWidth(){return dp(24);}public int getIntrinsicHeight(){return dp(24);}
        public void draw(Canvas c){float s=dp(24)/24f;Path p=new Path();if(done){p.moveTo(4*s,12*s);p.lineTo(10*s,18*s);p.lineTo(21*s,6*s);}else{p.moveTo(6*s,6*s);p.lineTo(18*s,18*s);p.moveTo(18*s,6*s);p.lineTo(6*s,18*s);}c.drawPath(p,ink);}
        public void setAlpha(int a){ink.setAlpha(a);}public void setColorFilter(ColorFilter f){ink.setColorFilter(f);}public int getOpacity(){return PixelFormat.TRANSLUCENT;}
    }
    /** The resting symbol: five rounded sound bars, the shape of a voice. */
    private final class Glyph extends android.graphics.drawable.Drawable {
        final Paint ink=new Paint(Paint.ANTI_ALIAS_FLAG);
        Glyph(){ink.setColor(Color.WHITE);ink.setStrokeWidth(dp(3));ink.setStrokeCap(Paint.Cap.ROUND);}
        public int getIntrinsicWidth(){return dp(28);}public int getIntrinsicHeight(){return dp(28);}
        public void draw(Canvas c){
            android.graphics.Rect b=getBounds();float cx=b.exactCenterX(),cy=b.exactCenterY();
            int[] tall={6,14,20,11,5};
            for(int i=0;i<tall.length;i++){float x=cx+(i-2)*dp(5.5f),h=dp(tall[i]);c.drawLine(x,cy-h/2,x,cy+h/2,ink);}
        }
        public void setAlpha(int a){ink.setAlpha(a);}public void setColorFilter(ColorFilter f){ink.setColorFilter(f);}public int getOpacity(){return PixelFormat.TRANSLUCENT;}
    }
    private void command(String command){
        try{owner.startForegroundService(new Intent(owner,BubbleService.class).putExtra("command",command));}
        catch(RuntimeException unavailable){state("Microphone unavailable");}
    }
    @Override public boolean dispatchTouchEvent(MotionEvent e){
        int action=e.getActionMasked();
        if(action==MotionEvent.ACTION_DOWN&&!listening&&!processing){
            if(settle!=null)settle.cancel();
            gesture=true;lifted=false;dragging=false;downX=e.getRawX();downY=e.getRawY();rest();
            main.postDelayed(lift,ViewConfiguration.getLongPressTimeout());return true;
        }
        if(gesture){
            // A tap starts a dictation. A long press picks the control up, and from then on it
            // follows the finger however slowly it moves. A quick slide moves it too. Holding
            // or moving it never records anything.
            if(action==MotionEvent.ACTION_MOVE&&(dragging||Math.hypot(e.getRawX()-downX,e.getRawY()-downY)>ViewConfiguration.get(owner).getScaledTouchSlop()))drag(e);
            if(action==MotionEvent.ACTION_UP||action==MotionEvent.ACTION_CANCEL){
                main.removeCallbacks(lift);gesture=false;
                boolean tap=action==MotionEvent.ACTION_UP&&!dragging&&!lifted;
                if(dragging)drop();
                lifted=false;ring(false);
                if(tap)command("bubble_start");
                rest();
            }
            return true;
        }
        return super.dispatchTouchEvent(e);
    }
    void state(String text){
        listening="Listening".equals(text);processing="Writing".equals(text)||"Recovering saved speech".equals(text);
        boolean expanded=listening||processing;
        mic.setVisibility(expanded?GONE:VISIBLE);waves.setVisibility(expanded?VISIBLE:GONE);
        cancel.setVisibility(expanded?VISIBLE:GONE);finish.setVisibility(listening?VISIBLE:GONE);
        waves.setContentDescription(processing?"Writing dictated words":"Listening to your voice");
        if(!listening){words.setText("");wordsBox.setVisibility(GONE);wordsBox.scrollTo(0,0);}
        waves.run(expanded,processing);
        if(!expanded)place();
        rest();refresh();
    }
    void refresh(){
        boolean locked=owner.getSystemService(KeyguardManager.class).isDeviceLocked();
        boolean enabled=owner.getSharedPreferences("dictation",Context.MODE_PRIVATE).getBoolean("bubble",true);
        setVisibility(!locked&&enabled&&(listening||processing||!TextTargetService.sensitive())?VISIBLE:GONE);
    }
    void preview(String text){
        if(!listening||processing)return;
        words.setText(text);wordsBox.setVisibility(text.isEmpty()?GONE:VISIBLE);
        // After the new words are laid out, slide all the way to the end so the newest line is
        // whole. The box clamps this to its real end; working the end out by hand leaves the
        // last line half hidden once the box has its own top padding.
        wordsBox.post(()->wordsBox.smoothScrollTo(0,words.getHeight()));
    }
    void level(float value){waves.heard(value);}
    void close(){main.removeCallbacks(lift);if(settle!=null)settle.cancel();waves.run(false,false);if(isAttachedToWindow())windows.removeView(this);}
    /**
     * Sound bars that follow the voice. They are redrawn every frame: the newest bar
     * rises with the voice at once, then each bar slides across the middle and settles.
     */
    private final class Waves extends View {
        private static final int BARS=17;
        private static final float STEP_MS=55f;
        final Paint ink=new Paint(Paint.ANTI_ALIAS_FLAG);
        private final float[] trail=new float[BARS+1];
        private volatile float heard;
        private float voice,travel,quiet=.02f,loud=.12f;
        private long last;
        private int count;
        private boolean running,busy;
        Waves(Context c){super(c);ink.setColor(Color.WHITE);ink.setStrokeWidth(dp(3));ink.setStrokeCap(Paint.Cap.ROUND);}
        /**
         * Measure the voice against this room and this microphone: the steady background
         * hum reads as silence and ordinary speech fills the bars, on any phone.
         */
        void heard(float value){
            quiet+=(value-quiet)*(value<quiet?.25f:.004f);
            loud=Math.max(Math.max(value,loud*.998f),quiet*3f+.04f);
            float base=quiet*1.5f+.004f;
            heard=Math.max(0,Math.min(1,(value-base)/(loud-base)));
        }
        void run(boolean on,boolean writing){
            busy=writing;
            if(on==running)return;
            running=on;last=0;heard=0;voice=0;travel=0;quiet=.02f;loud=.12f;java.util.Arrays.fill(trail,0);
            if(on)postInvalidateOnAnimation();
        }
        @Override protected void onDraw(Canvas c){
            super.onDraw(c);
            long now=SystemClock.uptimeMillis();float dt=last==0?16:Math.min(80,now-last);last=now;
            // Quiet speech should still move the bars; loud speech should not pin them.
            float goal=busy?.3f+.22f*(float)Math.sin(now/170.0):(float)Math.pow(heard,.8);
            // Rise quickly with the voice, ease back down.
            voice+=(goal-voice)*(1-(float)Math.exp(-dt/(goal>voice?25f:130f)));
            travel+=dt/STEP_MS;
            while(travel>=1){
                travel-=1;System.arraycopy(trail,1,trail,0,BARS);count++;
                // A little difference from bar to bar, so they read as a voice and not a meter.
                trail[BARS]=voice*(.72f+.28f*(float)Math.abs(Math.sin(count*2.4)));
            }
            trail[BARS]=Math.max(trail[BARS],voice*(.72f+.28f*(float)Math.abs(Math.sin((count+1)*2.4))));
            float middle=getHeight()/2f,pitch=getWidth()/(float)BARS,tallest=Math.min(getHeight()-dp(8f),dp(32f));
            for(int i=0;i<=BARS;i++){
                float x=(i+.5f-travel)*pitch;
                if(x<dp(1.5f)||x>getWidth()-dp(1.5f))continue;
                // Tallest through the middle, small and faint where bars enter and leave.
                float across=(float)Math.sin(Math.PI*x/getWidth());
                float h=dp(3f)+trail[i]*(.3f+.7f*across)*(tallest-dp(3f));
                ink.setAlpha((int)(255*Math.min(1,.25f+across)));
                c.drawLine(x,middle-h/2,x,middle+h/2,ink);
            }
            if(running)postInvalidateOnAnimation();
        }
    }
}
