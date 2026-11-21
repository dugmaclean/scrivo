package app.scrivo;

import android.content.SharedPreferences;
import java.io.*;
import java.net.*;
import org.json.JSONObject;

final class ServiceClient {
    static JSONObject request(SharedPreferences prefs,String path,JSONObject body) throws Exception {
        return request(prefs.getString("endpoint",""),prefs.getString("token",""),path,body,15000);
    }
    /** For the few requests where the server may rightly take longer to answer. */
    static JSONObject request(SharedPreferences prefs,String path,JSONObject body,int waitMs) throws Exception {
        return request(prefs.getString("endpoint",""),prefs.getString("token",""),path,body,waitMs);
    }
    /**
     * The server address as it will be saved, or null when it can't be used. Only https is
     * accepted, with no user name or password, query or fragment in it; trailing slashes are
     * dropped because every request path starts with one. A bare host name gets https:// added.
     */
    static String serverAddress(String typed){
        String value=typed==null?"":typed.trim();
        if(value.isEmpty()||value.matches(".*\\s.*"))return null;
        if(!value.contains("://"))value="https://"+value;
        else if(value.regionMatches(true,0,"https://",0,8))value="https://"+value.substring(8);
        value=value.replaceAll("/+$","");
        try{
            URL url=new URL(value);
            if(!"https".equals(url.getProtocol())||url.getUserInfo()!=null||url.getHost().isEmpty()
                ||url.getQuery()!=null||url.getRef()!=null||value.contains("?")||value.contains("#"))return null;
        }catch(MalformedURLException bad){return null;}
        return value;
    }
    /** The server refuses tokens shorter than 32 characters; spaces would break the header. */
    static boolean acceptableToken(String token){return token!=null&&token.length()>=32&&!token.matches(".*\\s.*");}
    private static JSONObject request(String root,String key,String path,JSONObject body,int waitMs) throws Exception {
        URL url=new URL(root+path);
        if(!"https".equals(url.getProtocol())||url.getUserInfo()!=null)throw new IOException();
        HttpURLConnection connection=(HttpURLConnection)url.openConnection();
        try {
            connection.setInstanceFollowRedirects(false);connection.setConnectTimeout(10000);connection.setReadTimeout(waitMs);
            if(!key.isEmpty())connection.setRequestProperty("Authorization","Bearer "+key);
            if(body!=null){
                byte[] data=body.toString().getBytes("UTF-8");connection.setRequestMethod("POST");connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type","application/json");connection.setFixedLengthStreamingMode(data.length);
                try(OutputStream out=connection.getOutputStream()){out.write(data);}
            }
            int code=connection.getResponseCode();
            if(code!=200)throw new Refused(code);
            try(InputStream in=connection.getInputStream()) {return new JSONObject(readText(in));}
        } finally {connection.disconnect();}
    }
    /** The server answered, but not with a yes; the number says which kind of no. */
    static final class Refused extends IOException {final int code;Refused(int c){code=c;}}
    static String readText(InputStream in) throws IOException {
        ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] chunk=new byte[8192];int read;
        while((read=in.read(chunk))!=-1){out.write(chunk,0,read);if(out.size()>1_000_000)throw new IOException();}
        return out.toString("UTF-8");
    }
}
