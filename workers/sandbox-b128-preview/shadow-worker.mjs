import build from './preview-manifest.json';
const PREFIX='/sandbox-test';
const allowed=new Set(Object.keys(build.files));
const headers=()=>({'X-Robots-Tag':'noindex, nofollow, noarchive','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','X-TAT-Build':build.commit,'Content-Security-Policy':"connect-src https://triple-a-tropics.com/sandbox-test/ https://cdn.triple-a-tropics.com/sandbox/warning-geography-v2.json blob: data:; form-action 'none'; frame-src https://triple-a-tropics.com/sandbox-test/wiki-view.html; object-src 'none'; base-uri 'self'"});
async function authorized(request,env){
    try{
        const raw=request.headers.get('Authorization')||'';
        if(!raw.startsWith('Basic '))return false;
        const credentials=atob(raw.slice(6)),colon=credentials.indexOf(':');
        if(credentials.slice(0,colon)!=='tester')return false;
        const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(credentials.slice(colon+1)));
        const hash=Array.from(new Uint8Array(digest),v=>v.toString(16).padStart(2,'0')).join('');
        return !!env.PREVIEW_PASSWORD_SHA256&&hash===env.PREVIEW_PASSWORD_SHA256;
    }catch{return false;}
}
export default{
    async fetch(request,env){
        const url=new URL(request.url),baseHeaders=headers();
        if(url.pathname!==PREFIX&&!url.pathname.startsWith(PREFIX+'/'))return new Response('Not found',{status:404,headers:baseHeaders});
        if(!await authorized(request,env))return new Response('B1.2.8 tester preview. Enter the tester credentials.',{status:401,headers:{...baseHeaders,'WWW-Authenticate':'Basic realm="TAT B1.2.8 Test Build", charset="UTF-8"','Cache-Control':'no-store'}});
        if(!['GET','HEAD'].includes(request.method))return new Response('Method not allowed',{status:405,headers:{...baseHeaders,Allow:'GET, HEAD'}});
        if(url.pathname===PREFIX)return new Response(null,{status:302,headers:{...baseHeaders,Location:PREFIX+'/'}});
        let name;try{name=decodeURIComponent(url.pathname.slice(PREFIX.length+1));}catch{return new Response('Not found',{status:404,headers:baseHeaders});}
        if(!name)name='index.html';
        if(name==='robots.txt')return new Response(request.method==='HEAD'?null:'User-agent: *\nDisallow: /\n',{headers:{...baseHeaders,'Content-Type':'text/plain'}});
        if(!allowed.has(name))return new Response('Not found',{status:404,headers:baseHeaders});
        if(name===build.sourceArchive){
            const h={...baseHeaders,'Content-Type':'application/gzip','Content-Length':String(build.sourceBytes),'Content-Disposition':'attachment; filename="wptc-corresponding-source.tar.gz"','Cache-Control':'private, max-age=600'};
            if(request.method==='HEAD')return new Response(null,{headers:h});
            let index=0,reader=null;
            const stream=new ReadableStream({async pull(controller){
                try{for(;;){
                    if(!reader){if(index===build.parts.length){controller.close();return;}const u=new URL('/'+build.parts[index++],url);const r=await env.ASSETS.fetch(u);if(!r.ok)throw new Error('Source archive unavailable');reader=r.body.getReader();}
                    const {done,value}=await reader.read();if(done){reader=null;continue;}controller.enqueue(value);return;
                }}catch(e){controller.error(e);}
            },async cancel(){if(reader)await reader.cancel();}});
            return new Response(stream,{headers:h});
        }
        const assetURL=new URL('/'+(name==='_redirects'?'__host-meta/redirects.txt':name),url);
        const response=await env.ASSETS.fetch(new Request(assetURL,{method:request.method}));
        const h=new Headers(response.headers);for(const [key,value]of Object.entries(baseHeaders))h.set(key,value);
        h.set('Cache-Control',name.endsWith('.html')?'private, no-cache, no-transform':'private, max-age=600, must-revalidate');
        return new Response(response.body,{status:response.status,headers:h});
    }
};
