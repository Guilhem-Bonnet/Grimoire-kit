// Les shaders du « slime d'encre » v3.1 (prototype WebGL2 validé le 2026-11-12, épique #1197) : raymarching sur
// champ de distance, sans bibliothèque. Repris tels quels du prototype (aucune retouche du GLSL) ; le module de
// rendu (`mascotte.js`) n'en pilote que les quatre formes du vocal (repos, écoute, réfléchit, parle). Les six autres
// formes (écrit, propose, pose une question, délègue, attend, livre) restent dans le champ de distance : le
// shader n'a pas à changer quand elles seront branchées.
//
// Des chaînes JavaScript, jamais un `<script>` ni un `<style>` : la CSP du cockpit est stricte.

export const VERTEX = `#version 300 es
  void main(){ vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2); gl_Position = vec4(p*2.0 - 1.0, 0.0, 1.0); }`;

export const FRAGMENT =`#version 300 es
  precision highp float;
  out vec4 o;
  uniform vec2 u_res;
  uniform float u_time, u_audio, u_yaw;
  uniform float u_w[10];
  uniform float u_bands[8];
  uniform float u_wobble, u_sy, u_sxz, u_spark, u_glow, u_swirl, u_exposure;
  uniform vec3 u_penTip, u_penX, u_penY, u_penZ, u_home;
  uniform float u_mainR, u_dotR, u_split, u_gulp;
  uniform vec4 u_cl[6];
  uniform vec2 u_nk[6];
  uniform vec4 u_sat[6];
  #define T u_time
  #define A u_audio
  const vec3 COLA = vec3(0.03, 0.04, 0.20);
  const vec3 COLB = vec3(0.30, 0.50, 1.00);

  vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}
  vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}
  vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}
  float snoise(vec3 v){
    const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);
    vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);
    vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);
    vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;
    i=mod289(i);
    vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));
    float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;
    vec4 j=p-49.0*floor(p*ns.z*ns.z);
    vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);
    vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);
    vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);
    vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));
    vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;
    vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);
    vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));
    p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;
    vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;
    return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));
  }
  float hash3(vec3 p){ p = fract(p*0.3183099 + 0.1); p *= 17.0; return fract(p.x*p.y*p.z*(p.x + p.y + p.z)); }
  mat2 rot(float a){ float c = cos(a), s = sin(a); return mat2(c, -s, s, c); }
  float smin(float a, float b, float k){ float h = max(k - abs(a - b), 0.0)/k; return min(a, b) - h*h*k*0.25; }
  float sdSphere(vec3 p, float r){ return length(p) - r; }
  float sdBox(vec3 p, vec3 b){ vec3 q = abs(p) - b; return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0); }
  float sdCapsule(vec3 p, vec3 a, vec3 b, float r){ vec3 pa = p - a, ba = b - a; float h = clamp(dot(pa, ba)/dot(ba, ba), 0.0, 1.0); return length(pa - ba*h) - r; }
  float sdRoundBox(vec3 p, vec3 b, float r){ vec3 q = abs(p) - b + r; return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0) - r; }
  float sdTorus(vec3 p, vec2 t){ vec2 q = vec2(length(p.xz) - t.x, p.y); return length(q) - t.y; }
  float sdCappedCone(vec3 p, float h, float r1, float r2){
    vec2 q = vec2(length(p.xz), p.y);
    vec2 k1 = vec2(r2, h); vec2 k2 = vec2(r2 - r1, 2.0*h);
    vec2 ca = vec2(q.x - min(q.x, (q.y < 0.0) ? r1 : r2), abs(q.y) - h);
    vec2 cb = q - k1 + k2*clamp(dot(k1 - q, k2)/dot(k2, k2), 0.0, 1.0);
    float s = (cb.x < 0.0 && ca.y < 0.0) ? -1.0 : 1.0;
    return s*sqrt(min(dot(ca, ca), dot(cb, cb)));
  }

  // 0 Au repos : sphère face à nous, qui respire
  float shRepos(vec3 p){
    vec3 q = p - vec3(0.0, 0.03*sin(T*0.9), 0.0);
    float L = length(q); vec3 n = q/max(L, 1e-4);
    float r = 0.7*(1.0 + 0.02*sin(T*1.25));
    return L - r - 0.012*snoise(n*1.2 + vec3(0.0, T*0.16, T*0.1));
  }

  // 1 Écoute : micro posé sur une onde d'encre qui suit la voix
  float waveF(float x){
    float e = 1.0 - x*x/1.5625; e *= e;
    float y = 0.014*sin(x*8.0 - T*2.4);
    y += u_bands[0]*0.13*sin(x*4.2 - T*6.0);
    y += u_bands[2]*0.09*sin(x*7.5 + T*8.5 + 1.0);
    y += u_bands[4]*0.06*sin(x*12.0 - T*11.0 + 2.0);
    y += u_bands[6]*0.04*sin(x*18.0 + T*14.0 + 3.0);
    return e*y;
  }
  float sdWave(vec3 p, float y0){
    float xc = clamp(p.x, -1.25, 1.25);
    float f = waveF(xc);
    float df = (waveF(xc + 0.01) - waveF(xc - 0.01))*50.0;
    float e = 1.0 - xc*xc/1.5625;
    vec3 q = vec3(p.x - xc, (p.y - y0 - f)/sqrt(1.0 + df*df), p.z);
    return length(q) - (0.016 + 0.034*e);
  }
  float shEcoute(vec3 p){
    vec3 q = p;
    float br = 1.0 + 0.035*A;
    float head = sdCapsule(q, vec3(0.0, 0.14, 0.0), vec3(0.0, 0.5, 0.0), 0.28*br);
    head += 0.008*(0.5 + 0.5*sin(q.y*58.0))*smoothstep(0.29, 0.34, q.y);
    head += 0.007*(0.5 + 0.5*sin(atan(q.z, q.x)*30.0))*smoothstep(0.21, 0.16, q.y)*smoothstep(-0.12, 0.04, q.y);
    float d = smin(head, sdTorus(q - vec3(0.0, 0.26, 0.0), vec2(0.28*br, 0.03)), 0.02);
    vec3 h = q - vec3(0.0, 0.26, 0.0);
    float u = length(vec2(length(h.xy) - 0.42, h.z)) - 0.038;
    u = max(u, h.y);
    d = min(d, u);
    d = smin(d, sdSphere(h - vec3(0.42, 0.0, 0.0), 0.058), 0.03);
    d = smin(d, sdSphere(h + vec3(0.42, 0.0, 0.0), 0.058), 0.03);
    d = smin(d, sdCapsule(h, vec3(-0.42, 0.0, 0.0), vec3(0.42, 0.0, 0.0), 0.022), 0.02);
    d = smin(d, sdCapsule(q, vec3(0.0, -0.15, 0.0), vec3(0.0, -0.82, 0.0), 0.048), 0.05);
    d = smin(d, sdWave(p, -0.88), 0.16);
    return d;
  }

  // 2 Réfléchit : nuage de pensée et bulles qui montent
  float shReflechit(vec3 p){
    vec3 q = p - vec3(0.12, 0.28, 0.0);
    q.z *= 1.25;
    float d = sdSphere(q - vec3(0.0, 0.16 + 0.04*sin(T*1.2), 0.0), 0.48 + 0.03*sin(T*2.1));
    d = smin(d, sdSphere(q - vec3(-0.46, -0.04, 0.04), 0.35 + 0.03*sin(T*1.7 + 1.0)), 0.24);
    d = smin(d, sdSphere(q - vec3(0.48, -0.02, -0.02), 0.37 + 0.03*sin(T*1.5 + 2.0)), 0.24);
    d = smin(d, sdSphere(q - vec3(-0.14, -0.2, 0.08), 0.33 + 0.02*sin(T*2.4 + 3.0)), 0.24);
    d = smin(d, sdSphere(q - vec3(0.24, -0.22, 0.06), 0.32 + 0.02*sin(T*1.9 + 4.0)), 0.24);
    d /= 1.25;
    for (int i = 0; i < 3; i++) {
      float fi = float(i);
      float ph = fract(T*0.35 - fi*0.33);
      vec3 c = mix(vec3(-0.95, -0.95, 0.12), vec3(-0.5, -0.4, 0.08), ph);
      float r = mix(0.07, 0.17, ph) * smoothstep(0.0, 0.12, ph) * (1.0 - smoothstep(0.9, 1.0, ph));
      d = min(d, sdSphere(p - c, r + 0.001));
    }
    return d;
  }

  // 3 Parle : sphère, contour déformé par les graves, surface qui frissonne avec les aigus
  float shParle(vec3 p){
    vec3 q = p - vec3(0.0, 0.02, 0.0);
    float L = length(q); vec3 n = q/max(L, 1e-4);
    // Lobes du contour : polynômes harmoniques de (x, y), nuls et lisses au centre de la face
    vec2 z1 = n.xy;
    vec2 z2 = vec2(z1.x*z1.x - z1.y*z1.y, 2.0*z1.x*z1.y);
    vec2 z3 = vec2(z2.x*z1.x - z2.y*z1.y, z2.x*z1.y + z2.y*z1.x);
    vec2 z4 = vec2(z2.x*z2.x - z2.y*z2.y, 2.0*z2.x*z2.y);
    float lob = u_bands[1]*dot(z2, vec2(sin(T*1.7), cos(T*1.7)))
              + u_bands[2]*dot(z3, vec2(sin(1.0 - T*2.3), cos(1.0 - T*2.3)))
              + u_bands[3]*0.8*dot(z4, vec2(sin(2.0 + T*2.9), cos(2.0 + T*2.9)));
    float wob = snoise(n*1.3 + vec3(0.0, 0.0, T*0.9));
    float fine = sin(n.x*8.0 + T*5.0)*sin(n.y*7.0 - T*4.3)*sin(n.z*6.0 + T*3.1);
    float r = 0.7*(1.0 + 0.045*A);
    return L - r - 0.06*lob - 0.04*A*wob - 0.014*(u_bands[5] + u_bands[6])*fine;
  }

  // 4 Écrit : stylo plume détaillé, la pointe touche le sol
  vec3 penQ(vec3 p){ vec3 d = p - u_penTip; return vec3(dot(d, u_penX), dot(d, u_penY), dot(d, u_penZ)); }
  void penParts(vec3 q, out float ink, out float gold){
    float h = clamp(q.y/0.36, 0.0, 1.0);
    float rw = mix(0.005, 0.082, sqrt(h));
    float nib = (length(vec2(q.x, q.z*2.4)) - rw)*0.41;
    nib = max(nib, max(-q.y, q.y - 0.38));
    nib = max(nib, -sdBox(q - vec3(0.0, 0.1, 0.0), vec3(0.0045, 0.12, 0.3)));
    nib = max(nib, -(length(q.xy - vec2(0.0, 0.225)) - 0.019));
    float feed = sdCapsule(q, vec3(0.0, 0.1, -0.03), vec3(0.0, 0.36, -0.035), 0.028);
    float grip = sdCappedCone(q - vec3(0.0, 0.47, 0.0), 0.15, 0.084, 0.118);
    grip -= 0.004*smoothstep(0.45, 0.5, q.y)*smoothstep(0.58, 0.53, q.y);
    float ring = sdTorus(q - vec3(0.0, 0.645, 0.0), vec2(0.124, 0.021));
    float barrel = sdCapsule(q, vec3(0.0, 0.68, 0.0), vec3(0.0, 1.46, 0.0), 0.133);
    float band = sdTorus(q - vec3(0.0, 1.3, 0.0), vec2(0.133, 0.015));
    float clip = sdRoundBox(q - vec3(0.0, 1.2, 0.156), vec3(0.027, 0.23, 0.013), 0.012);
    clip = min(clip, sdSphere(q - vec3(0.0, 0.975, 0.163), 0.031));
    clip = min(clip, sdRoundBox(q - vec3(0.0, 1.42, 0.128), vec3(0.03, 0.035, 0.034), 0.012));
    ink = min(smin(grip, barrel, 0.03), feed);
    gold = min(min(nib, ring), min(band, clip));
  }
  float shEcrit(vec3 p){ float a, b; penParts(penQ(p), a, b); return min(a, b); }
  float penGold(vec3 p){ float a, b; penParts(penQ(p), a, b); return smoothstep(0.01, -0.004, b - a); }

  // 5 Propose : ampoule, verre ambré et culot doré
  vec3 bulbQ(vec3 p){ return p - vec3(0.0, 0.1 + 0.04*sin(T*1.5), 0.0); }
  float shPropose(vec3 p){
    vec3 q = bulbQ(p);
    float glass = sdSphere(q - vec3(0.0, 0.3, 0.0), 0.5);
    glass = smin(glass, sdCappedCone(q - vec3(0.0, -0.22, 0.0), 0.2, 0.19, 0.34), 0.14);
    float thr = 0.017*sin(q.y*52.0 + atan(q.z, q.x));
    float screw = max(length(q.xz) - 0.19 - thr, abs(q.y + 0.6) - 0.17)*0.8;
    float tip = sdCappedCone(q - vec3(0.0, -0.82, 0.0), 0.06, 0.06, 0.15);
    return min(smin(glass, screw, 0.03), tip);
  }
  float filament(vec3 q){
    vec3 f = q - vec3(0.0, 0.32, 0.0);
    float xc = clamp(f.x, -0.15, 0.15);
    float coil = length(vec3(f.x - xc, f.y - 0.028*sin(xc*75.0), f.z));
    float wire = min(sdCapsule(q, vec3(-0.15, 0.32, 0.0), vec3(-0.07, -0.32, 0.0), 0.0),
                     sdCapsule(q, vec3(0.15, 0.32, 0.0), vec3(0.07, -0.32, 0.0), 0.0));
    return exp(-coil*38.0) + 0.25*exp(-wire*70.0);
  }

  // 6 Question : bulle penchée, une goutte s'étire vers toi
  float shQuestion(vec3 p){
    vec3 q = p; q.xy *= rot(0.2 + 0.06*sin(T*0.8));
    q -= vec3(0.0, 0.22, 0.0);
    float d = sdRoundBox(q, vec3(0.62, 0.42, 0.2), 0.24);
    float st = 0.5 + 0.5*sin(T*1.1);
    vec3 a = vec3(0.32, -0.36, 0.05);
    vec3 b = a + vec3(0.12, -0.28 - 0.28*st, 0.16);
    d = smin(d, sdCapsule(q, a, b, 0.045), 0.12);
    d = smin(d, sdSphere(q - b - vec3(0.0, -0.06, 0.0), 0.1 + 0.035*st), 0.08);
    return d;
  }

  // 7 Délègue : le corps principal, les bourgeons sont ajoutés dans map()
  float shDelegue(vec3 p){ return length(p - u_home) - u_mainR; }

  // 8 Attend : trois points qui sautillent, comme un chargement
  float shAttend(vec3 p){
    float open = u_split*(1.0 - u_gulp);
    float sep = 0.42*open;
    float k = mix(0.03, 0.55, clamp(1.0 - open, 0.0, 1.0));
    float r0 = mix(mix(u_mainR*0.82, u_dotR, u_split), u_dotR*1.45, u_gulp);
    float d = 1e3;
    for (int i = 0; i < 3; i++) {
      float fi = float(i);
      float up = pow(max(0.0, sin(T*4.4 - fi*0.95)), 3.0)*open;
      vec3 q = p - u_home - vec3((fi - 1.0)*sep, 0.16*up, 0.0);
      q.y /= 1.0 + 0.22*up; q.xz *= 1.0 + 0.08*up;
      d = smin(d, length(q) - r0*(1.0 + 0.1*up), k);
    }
    return d;
  }

  // 9 Livre : coche
  float shLivre(vec3 p){
    vec3 q = p - vec3(0.02, 0.0, 0.0);
    float d = sdCapsule(q, vec3(-0.52, 0.02, 0.0), vec3(-0.12, -0.36, 0.0), 0.15);
    d = smin(d, sdCapsule(q, vec3(-0.12, -0.36, 0.0), vec3(0.6, 0.5, 0.0), 0.15), 0.08);
    return d;
  }

  float shape(vec3 p){
    float acc = 0.0, ws = 0.0;
    if (u_w[0] > 0.002) { acc += u_w[0]*shRepos(p); ws += u_w[0]; }
    if (u_w[1] > 0.002) { acc += u_w[1]*shEcoute(p); ws += u_w[1]; }
    if (u_w[2] > 0.002) { acc += u_w[2]*shReflechit(p); ws += u_w[2]; }
    if (u_w[3] > 0.002) { acc += u_w[3]*shParle(p); ws += u_w[3]; }
    if (u_w[4] > 0.002) { acc += u_w[4]*shEcrit(p); ws += u_w[4]; }
    if (u_w[5] > 0.002) { acc += u_w[5]*shPropose(p); ws += u_w[5]; }
    if (u_w[6] > 0.002) { acc += u_w[6]*shQuestion(p); ws += u_w[6]; }
    if (u_w[7] > 0.002) { acc += u_w[7]*shDelegue(p); ws += u_w[7]; }
    if (u_w[8] > 0.002) { acc += u_w[8]*shAttend(p); ws += u_w[8]; }
    if (u_w[9] > 0.002) { acc += u_w[9]*shLivre(p); ws += u_w[9]; }
    return acc/max(ws, 0.001);
  }
  vec3 warp(vec3 p){ vec3 q = p; q.y = (q.y + 1.1)/u_sy - 1.1; q.xz /= u_sxz; return q; }
  float map(vec3 p){
    vec3 q = warp(p);
    float d = shape(q);
    d -= (0.004 + 0.05*u_wobble)*sin(q.x*7.0 + T*7.0)*sin(q.y*6.0 - T*6.0)*sin(q.z*5.0 + T*8.0);
    d *= min(u_sy, u_sxz);
    for (int i = 0; i < 6; i++) {
      vec4 c = u_cl[i];
      if (c.w < 0.004) continue;
      float dc = length(p - c.xyz) - c.w;
      float rn = u_nk[i].x;
      if (rn > 0.002) dc = smin(dc, sdCapsule(p, u_home, c.xyz, rn), 0.1);
      float kk = u_nk[i].y;
      d = kk > 0.003 ? smin(d, dc, kk) : min(d, dc);
      vec4 s = u_sat[i];
      if (s.w > 0.004) d = min(d, length(p - s.xyz) - s.w);
    }
    return d;
  }
  vec3 calcN(vec3 p){
    const vec2 e = vec2(0.0012, -0.0012);
    return normalize(e.xyy*map(p + e.xyy) + e.yyx*map(p + e.yyx) + e.yxy*map(p + e.yxy) + e.xxx*map(p + e.xxx));
  }
  float paintGold(vec3 p){
    vec3 q = warp(p);
    float g = 0.0;
    if (u_w[4] > 0.5) g += smoothstep(0.6, 0.96, u_w[4])*penGold(q);
    if (u_w[5] > 0.5) { vec3 b = bulbQ(q); g += smoothstep(0.6, 0.96, u_w[5])*(1.0 - smoothstep(-0.47, -0.41, b.y))*smoothstep(-0.8, -0.76, b.y); }
    return clamp(g, 0.0, 1.0);
  }
  float paintGlass(vec3 p){
    if (u_w[5] < 0.5) return 0.0;
    vec3 b = bulbQ(warp(p));
    return clamp(smoothstep(0.6, 0.96, u_w[5])*smoothstep(-0.47, -0.41, b.y)*clamp(u_glow*1.3, 0.0, 1.0), 0.0, 1.0);
  }

  void main(){
    vec2 uv = (gl_FragCoord.xy - 0.5*u_res)/u_res.y;
    vec3 ro = vec3(4.1*sin(u_yaw), 0.3, 4.1*cos(u_yaw));
    vec3 ta = vec3(0.0, -0.12, 0.0);
    vec3 ww = normalize(ta - ro), uu = normalize(cross(ww, vec3(0.0, 1.0, 0.0))), vv = cross(uu, ww);
    vec3 rd = normalize(uv.x*uu + uv.y*vv + 1.75*ww);
    float t = 0.0, minD = 9.0; bool hit = false;
    for (int i = 0; i < 110; i++) {
      float d = map(ro + rd*t);
      minD = min(minD, d);
      if (d < 0.0012) { hit = true; break; }
      t += d*0.8;
      if (t > 8.0) break;
    }
    vec3 warm = vec3(1.0, 0.7, 0.28);
    float halo = u_glow*smoothstep(0.6, 0.96, u_w[5])*exp(-max(minD, 0.0)*4.0);
    if (!hit) {
      float a = 0.0;
      if (rd.y < 0.0) {
        float tp = (-1.1 - ro.y)/rd.y; vec3 pp = ro + rd*tp;
        float dm = map(pp + vec3(0.0, 0.3, 0.0));
        a = (1.0 - smoothstep(0.0, 0.75, dm))*0.55*smoothstep(3.0, 0.8, length(pp.xz));
      }
      float ha = clamp(halo*0.55, 0.0, 1.0);
      o = vec4(warm*ha, max(a*(1.0 - ha), ha));
      return;
    }
    vec3 p = ro + rd*t;
    vec3 n = calcN(p);
    vec3 v = -rd;
    float ndv = max(dot(n, v), 0.0);
    float fres = pow(1.0 - ndv, 3.0);
    float th = clamp(-map(p - n*0.3)/0.3, 0.0, 1.0);
    vec3 L = normalize(vec3(-0.5, 0.75, 0.55));
    vec3 r = reflect(rd, n);
    vec3 env = mix(vec3(0.02, 0.02, 0.07), vec3(0.32, 0.4, 0.95), smoothstep(-0.3, 1.0, r.y));
    env += vec3(0.9, 0.95, 1.0)*smoothstep(0.9, 0.985, dot(r, L))*1.5;
    vec3 rf = refract(rd, n, 0.76);
    vec3 envR = mix(vec3(0.03, 0.03, 0.12), vec3(0.5, 0.6, 1.0), smoothstep(-0.6, 1.0, rf.y));
    vec3 body = mix(COLB, COLA, th);
    vec3 col = body*(0.35 + 0.9*envR)*(1.0 - 0.5*th);
    float sss = pow(clamp(dot(v, -L)*0.5 + 0.5, 0.0, 1.0), 2.0)*(1.0 - th);
    col += COLB*sss*0.35;
    float sp = 0.0;
    vec3 flow = vec3(sin(T*0.3*u_swirl + p.y*2.0), T*0.12*u_swirl, cos(T*0.27*u_swirl + p.x*2.0))*0.35;
    for (int i = 1; i <= 5; i++) {
      vec3 q = (p + rf*float(i)*0.08 + flow)*18.0;
      vec3 cell = floor(q); vec3 f = fract(q) - 0.5;
      float h = hash3(cell);
      vec3 off = vec3(hash3(cell + 1.3), hash3(cell + 2.7), hash3(cell + 5.1)) - 0.5;
      float tw = 0.55 + 0.45*sin(T*3.0 + h*40.0);
      sp += step(0.7, h)*smoothstep(0.17, 0.0, length(f - off*0.6))*tw/float(i);
    }
    col += mix(COLB, vec3(1.0), 0.55)*sp*u_spark*1.15;
    vec3 ip = p + rf*0.32 + flow*0.5;
    float c1 = snoise(ip*1.6 + vec3(0.0, -T*0.11*u_swirl, T*0.07*u_swirl));
    float c2 = snoise(ip*2.9 - vec3(T*0.09*u_swirl, 0.0, T*0.06));
    float veins = smoothstep(0.05, 0.95, c1*0.65 + c2*0.35 + 0.3);
    float life = 0.2 + 0.55*A*u_w[3] + 0.1*(u_swirl - 1.0);
    col += mix(vec3(0.14, 0.22, 0.8), vec3(0.42, 0.3, 0.95), c2*0.5 + 0.5)*veins*th*life;
    col = mix(col, env, 0.06 + fres*0.75);
    col += vec3(1.0)*pow(max(dot(r, L), 0.0), 60.0)*1.6;
    float mG = paintGold(p);
    if (mG > 0.001) {
      vec3 gold = vec3(1.0, 0.75, 0.34);
      vec3 envG = mix(vec3(0.12, 0.08, 0.04), vec3(1.0, 0.93, 0.78), smoothstep(-0.5, 1.0, r.y));
      vec3 cg = gold*(0.16 + 0.95*envG) + gold*fres*0.6 + vec3(1.0, 0.95, 0.85)*pow(max(dot(r, L), 0.0), 36.0)*1.6;
      col = mix(col, cg, mG);
    }
    float mL = paintGlass(p);
    if (mL > 0.001) {
      float fil = 0.0;
      for (int i = 1; i <= 10; i++) fil += filament(bulbQ(warp(p + rf*0.085*float(i))));
      fil *= 0.22;
      vec3 amber = mix(vec3(0.95, 0.42, 0.06), vec3(1.0, 0.9, 0.55), 1.0 - th);
      vec3 cl = amber*(0.25 + 1.25*u_glow) + vec3(1.0, 0.88, 0.6)*fil*u_glow*2.2;
      cl += warm*sp*u_spark*0.8;
      cl = mix(cl, env, fres*0.35);
      cl += vec3(1.0)*pow(max(dot(r, L), 0.0), 120.0)*1.4;
      col = mix(col, cl, mL);
    }
    col *= u_exposure;
    col = col/(1.0 + col*0.3);
    o = vec4(pow(col, vec3(0.92)), 1.0);
  }`;
