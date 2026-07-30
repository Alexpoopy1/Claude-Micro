#!/usr/bin/env python3
"""Build the single-file HTML viewer for the whole project.

Everything is embedded: the 3D geometry (quantised and base64'd), the routed
board, the schematic, the BOM and every check result. No network, no CDN, no
build step -- open build/claude-micro.html in a browser.
"""

from __future__ import annotations

import base64
import csv
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mesh as M          # noqa: E402
import parts              # noqa: E402
import schematic          # noqa: E402
from device import load, ensure_build, BUILD   # noqa: E402


# --------------------------------------------------------------------------
# geometry payload
# --------------------------------------------------------------------------

def encode_mesh(m: M.Mesh) -> dict:
    """Quantise a mesh to 16-bit positions over its own bounding box.

    Normals are omitted: the shader derives flat normals from screen-space
    derivatives, which halves the payload and suits the faceted look.
    """
    lo, hi = m.bbox()
    span = [max(1e-6, hi[i] - lo[i]) for i in range(3)]
    buf = bytearray()
    for tri in m.tris:
        for p in tri:
            for k in range(3):
                q = int(round((p[k] - lo[k]) / span[k] * 65535.0))
                buf += struct.pack("<H", max(0, min(65535, q)))
    return {"lo": [round(v, 4) for v in lo], "span": [round(v, 6) for v in span],
            "n": len(m.tris) * 3,
            "d": base64.b64encode(bytes(buf)).decode()}


def build_scene(cfg: dict) -> dict:
    items = []
    for it in parts.assembly(cfg):
        v = it["mesh"].validate(require_closed=False)
        items.append({
            "name": it["name"], "color": it["color"], "group": it["group"],
            "explode": it["explode"], "opacity": it["opacity"],
            "tris": v["triangles"], "geo": encode_mesh(it["mesh"]),
        })
    lo = [min(i["geo"]["lo"][k] for i in items) for k in range(3)]
    hi = [max(i["geo"]["lo"][k] + i["geo"]["span"][k] for i in items) for k in range(3)]
    return {"parts": items, "bbox": [lo, hi],
            "center": [(lo[k] + hi[k]) / 2 for k in range(3)]}


def merge_pour_rects(runs: list) -> list:
    """Collapse scanline pour runs into rectangles so the viewer draws
    hundreds of shapes instead of thousands of lines."""
    by_y = {}
    for y, x0, x1 in runs:
        by_y.setdefault(round(y, 3), []).append((x0, x1))
    ys = sorted(by_y)
    if not ys:
        return []
    step = min((ys[i + 1] - ys[i] for i in range(len(ys) - 1)), default=0.2) or 0.2
    open_rects: dict[tuple, list] = {}
    out = []
    for y in ys:
        cur = {(round(a, 3), round(b, 3)) for a, b in by_y[y]}
        for key in list(open_rects):
            if key in cur:
                open_rects[key][1] = y
            else:
                x0, x1 = key
                y0, y1 = open_rects.pop(key)
                out.append([x0, y0 - step / 2, x1, y1 + step / 2])
        for key in cur:
            if key not in open_rects:
                open_rects[key] = [y, y]
    for key, (y0, y1) in open_rects.items():
        out.append([key[0], y0 - step / 2, key[1], y1 + step / 2])
    return [[round(v, 3) for v in r] for r in out]


# --------------------------------------------------------------------------

def read_json(*p):
    path = os.path.join(BUILD, *p)
    with open(path) as fh:
        return json.load(fh)


def read_csv(*p):
    with open(os.path.join(BUILD, *p)) as fh:
        return list(csv.DictReader(fh))


def build(verbose: bool = True) -> str:
    cfg = load()
    scene = build_scene(cfg)
    board = read_json("pcb", "board.json")
    board["pours"] = {k: merge_pour_rects(v) for k, v in board["pours"].items()}
    drc = read_json("pcb", "drc_report.json")
    stl = read_json("stl_report.json")
    bom = read_csv("pcb", "bom.csv")
    svg = schematic.render(cfg)

    payload = {
        "cfg": {
            "meta": cfg["meta"], "layout": {
                "keys": cfg["layout"]["keys"],
                "encoder": cfg["layout"]["encoder"],
                "joystick": cfg["layout"]["joystick"],
                "touch": cfg["layout"]["touch_strip"],
                "logo": cfg["layout"]["logo"],
            },
            "stack": cfg["stack"], "case": cfg["case"], "pcb": cfg["pcb"],
            "rgb": {k: v for k, v in cfg["rgb"].items() if k != "chain"},
            "matrix": {k: v for k, v in cfg["matrix"].items() if k != "nodes"},
            "module": cfg["module"], "layers": cfg["layers"], "colors": cfg["colors"],
            "pins": cfg["pins"],
        },
        "scene": scene, "board": board, "drc": drc, "stl": stl, "bom": bom,
        "logo": M.claude_burst(0, 0, 100),
    }

    html = TEMPLATE.replace("/*__DATA__*/", json.dumps(payload, separators=(",", ":")))
    html = html.replace("<!--__SCHEMATIC__-->", svg)
    out = os.path.join(ensure_build(), "claude-micro.html")
    with open(out, "w") as fh:
        fh.write(html)
    if verbose:
        print(f"  wrote {out}  ({len(html) / 1024:.0f} KB, "
              f"{sum(p['tris'] for p in scene['parts']):,} triangles)")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Claude Micro - agent macropad</title>
<style>
:root{
  --bg:#0f1115; --bg2:#161920; --bg3:#1d212a; --line:#2a2f3a;
  --fg:#e7e9ee; --fg2:#9aa3b2; --accent:#d97757; --accent2:#6ea8fe;
  --ok:#3fb27f; --warn:#e0a33e; --err:#e0605c;
}
*{box-sizing:border-box}
html,body{margin:0;height:100%}
body{background:var(--bg);color:var(--fg);font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
     display:flex;flex-direction:column;overflow:hidden}
a{color:var(--accent)}
header{display:flex;align-items:center;gap:14px;padding:10px 18px;background:var(--bg2);
       border-bottom:1px solid var(--line);flex:0 0 auto}
.mark{width:26px;height:26px;flex:0 0 auto}
.mark path{fill:var(--accent)}
h1{font-size:15px;margin:0;font-weight:650;letter-spacing:.2px}
h1 small{color:var(--fg2);font-weight:400;margin-left:8px;font-size:12px}
.spacer{flex:1}
.pill{font-size:11px;padding:3px 9px;border-radius:99px;background:var(--bg3);color:var(--fg2);
      border:1px solid var(--line);white-space:nowrap}
.pill.ok{color:var(--ok);border-color:#1f4636}
nav{display:flex;gap:2px;padding:0 12px;background:var(--bg2);border-bottom:1px solid var(--line);
    flex:0 0 auto;overflow-x:auto}
nav button{background:none;border:0;border-bottom:2px solid transparent;color:var(--fg2);
  padding:9px 14px;font:inherit;font-size:13px;cursor:pointer;white-space:nowrap}
nav button:hover{color:var(--fg)}
nav button.on{color:var(--fg);border-bottom-color:var(--accent)}
main{flex:1;min-height:0;position:relative}
.tab{position:absolute;inset:0;display:none;overflow:auto}
.tab.on{display:flex}
.split{display:flex;width:100%;height:100%;min-height:0}
.side{width:290px;flex:0 0 auto;background:var(--bg2);border-right:1px solid var(--line);
      overflow-y:auto;padding:14px}
.side.right{border-right:0;border-left:1px solid var(--line)}
.stage{flex:1;min-width:0;position:relative;background:radial-gradient(ellipse at 50% 30%,#1a1e26,#0d0f13 70%)}
canvas{display:block;width:100%;height:100%;touch-action:none}
.grp{margin-bottom:18px}
.grp h3{font-size:11px;text-transform:uppercase;letter-spacing:.09em;color:var(--fg2);
        margin:0 0 9px;font-weight:600}
label.row{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:13px;cursor:pointer}
label.row input{accent-color:var(--accent);cursor:pointer}
.sw{width:11px;height:11px;border-radius:3px;flex:0 0 auto;border:1px solid rgba(255,255,255,.18)}
.nm{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ct{color:var(--fg2);font-size:11px;font-variant-numeric:tabular-nums}
input[type=range]{width:100%;accent-color:var(--accent)}
.btns{display:flex;flex-wrap:wrap;gap:6px}
.btns button,.mini{background:var(--bg3);color:var(--fg);border:1px solid var(--line);
  border-radius:6px;padding:5px 10px;font:inherit;font-size:12px;cursor:pointer}
.btns button:hover,.mini:hover{border-color:var(--accent);color:var(--accent)}
.hud{position:absolute;left:12px;bottom:12px;font-size:11px;color:var(--fg2);
     background:rgba(15,17,21,.78);border:1px solid var(--line);border-radius:7px;padding:7px 10px;
     pointer-events:none;font-variant-numeric:tabular-nums}
.pad{padding:22px 26px;max-width:1100px}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--fg2);font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:.06em;
   position:sticky;top:0;background:var(--bg)}
td.num{text-align:right;font-variant-numeric:tabular-nums}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:12px;margin:16px 0 24px}
.card{background:var(--bg2);border:1px solid var(--line);border-radius:9px;padding:12px 14px}
.card .k{font-size:11px;color:var(--fg2);text-transform:uppercase;letter-spacing:.06em}
.card .v{font-size:20px;font-weight:600;margin-top:3px;font-variant-numeric:tabular-nums}
.card .v.small{font-size:14px;font-weight:500}
.finding{display:flex;gap:10px;padding:7px 10px;border-radius:7px;background:var(--bg2);
  border:1px solid var(--line);margin-bottom:6px;font-size:13px;align-items:flex-start}
.tag{font-size:10px;padding:2px 7px;border-radius:99px;flex:0 0 auto;margin-top:1px;
     text-transform:uppercase;letter-spacing:.05em;font-weight:600}
.tag.info{background:#16342a;color:var(--ok)} .tag.warning{background:#3a2f16;color:var(--warn)}
.tag.error{background:#3a1d1d;color:var(--err)}
.k2{color:var(--fg2);font-size:12px}
h2{font-size:17px;margin:26px 0 10px;font-weight:650}
h2:first-child{margin-top:0}
p{color:var(--fg2);max-width:74ch}
ol.steps{padding-left:20px} ol.steps li{margin-bottom:10px;max-width:74ch}
pre{background:var(--bg2);border:1px solid var(--line);border-radius:8px;padding:12px 14px;
    overflow:auto;font-size:12px;line-height:1.5}
svg.schem{width:100%;height:auto;background:var(--bg2);border:1px solid var(--line);border-radius:9px}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12px;color:var(--fg2);margin-top:10px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px}
@media (max-width:820px){.side{width:220px}.split{flex-direction:column}.side{width:100%;height:auto;max-height:40%}}
</style></head><body>

<header>
  <svg class="mark" id="mark" viewBox="-50 -50 100 100" aria-label="Claude"></svg>
  <h1>Claude Micro <small id="sub"></small></h1>
  <div class="spacer"></div>
  <span class="pill" id="pillGeom"></span>
  <span class="pill" id="pillPcb"></span>
  <span class="pill" id="pillDrc"></span>
</header>

<nav id="nav"></nav>
<main id="main"></main>

<template id="tpl-model">
<div class="split">
  <div class="side">
    <div class="grp">
      <h3>Exploded view</h3>
      <input type="range" id="explode" min="0" max="100" value="0">
      <div class="btns" style="margin-top:8px">
        <button data-ex="0">Assembled</button>
        <button data-ex="55">Exploded</button>
        <button data-ex="100">Full</button>
      </div>
    </div>
    <div class="grp">
      <h3>View</h3>
      <div class="btns">
        <button data-view="iso">Iso</button><button data-view="top">Top</button>
        <button data-view="front">Front</button><button data-view="side">Side</button>
        <button id="spin">Spin</button><button id="wire">Wireframe</button>
      </div>
    </div>
    <div class="grp">
      <h3>Parts</h3>
      <div id="partlist"></div>
    </div>
    <div class="grp">
      <h3>Groups</h3>
      <div id="grouplist"></div>
    </div>
  </div>
  <div class="stage"><canvas id="gl"></canvas>
    <div class="hud" id="hud"></div>
  </div>
</div>
</template>

<template id="tpl-pcb">
<div class="split">
  <div class="side">
    <div class="grp"><h3>Layers</h3><div id="layerlist"></div></div>
    <div class="grp"><h3>View</h3>
      <div class="btns">
        <button id="pcbFit">Fit</button><button id="pcbFlip">Flip board</button>
      </div>
      <label class="row" style="margin-top:8px"><input type="checkbox" id="pcbRefs"> <span class="nm">Reference designators</span></label>
    </div>
    <div class="grp"><h3>Board</h3><div id="pcbstats" class="k2"></div></div>
    <div class="grp"><h3>Highlight net</h3>
      <select id="netsel" class="mini" style="width:100%"></select>
    </div>
  </div>
  <div class="stage"><canvas id="pcb"></canvas><div class="hud" id="pcbhud"></div></div>
</div>
</template>

<script>
const DATA = /*__DATA__*/;
const SCHEMATIC = document.createElement('template');

/* ---------------- Claude mark ---------------- */
(function(){
  const svg = document.getElementById('mark');
  svg.innerHTML = DATA.logo.map(b =>
    '<path d="M' + b.map(p => p[0].toFixed(2)+' '+p[1].toFixed(2)).join('L') + 'Z"/>').join('');
})();
document.getElementById('sub').textContent =
  DATA.cfg.meta.description.replace(/\.$/,'') + '  ·  v' + DATA.cfg.meta.version;

/* ---------------- tiny helpers ---------------- */
const $ = (s,r)=> (r||document).querySelector(s);
const el = (t,c,x)=>{const e=document.createElement(t); if(c)e.className=c; if(x!=null)e.textContent=x; return e;};
const fmt = n => n.toLocaleString();

/* ---------------- tabs ---------------- */
const TABS = [
  ['model','3D model'], ['pcb','PCB'], ['schem','Schematic'],
  ['bom','Bill of materials'], ['print','Print & assemble'],
  ['fw','Firmware'], ['verify','Verification']
];
const nav = document.getElementById('nav'), main = document.getElementById('main');
const panes = {};
TABS.forEach(([id,label],i)=>{
  const b = el('button',null,label); b.onclick = ()=>show(id); b.dataset.id=id; nav.appendChild(b);
  const d = el('div','tab'); d.id='tab-'+id; main.appendChild(d); panes[id]=d;
});
let current=null, inited={};
function show(id){
  current=id;
  [...nav.children].forEach(b=>b.classList.toggle('on', b.dataset.id===id));
  TABS.forEach(([t])=>panes[t].classList.toggle('on', t===id));
  if(!inited[id]){ inited[id]=true; BUILD[id] && BUILD[id](panes[id]); }
  if(id==='model') resizeGL();
  if(id==='pcb') drawPCB();
}

/* ================= 3D viewer ================= */
const V = {
  gl:null, prog:null, parts:[], az:3.76, el:0.52, dist:0, target:[0,0,0],
  explode:0, wire:false, spin:false, hidden:new Set(), hover:-1
};

function decodeGeo(g){
  const raw = atob(g.d), n = g.n, out = new Float32Array(n*3);
  for(let i=0;i<n*3;i++){
    const b = raw.charCodeAt(i*2) | (raw.charCodeAt(i*2+1)<<8);
    out[i] = g.lo[i%3] + b/65535*g.span[i%3];
  }
  return out;
}
const hex2rgb = h => [parseInt(h.slice(1,3),16)/255, parseInt(h.slice(3,5),16)/255, parseInt(h.slice(5,7),16)/255];

const VS = `#version 300 es
in vec3 aPos; uniform mat4 uMVP; uniform mat4 uModel; out vec3 vWorld;
void main(){ vec4 w = uModel*vec4(aPos,1.0); vWorld=w.xyz; gl_Position = uMVP*vec4(aPos,1.0); }`;
const FS = `#version 300 es
precision highp float; in vec3 vWorld; out vec4 frag;
uniform vec3 uColor; uniform float uAlpha; uniform float uHi;
void main(){
  vec3 n = normalize(cross(dFdx(vWorld), dFdy(vWorld)));
  vec3 l1 = normalize(vec3(0.45,-0.6,0.85));
  vec3 l2 = normalize(vec3(-0.7,0.35,0.35));
  float d = max(dot(n,l1),0.0)*0.82 + max(dot(n,l2),0.0)*0.28 + 0.24;
  vec3 v = normalize(vec3(0.0,0.0,1.0));
  float s = pow(max(dot(reflect(-l1,n),v),0.0), 24.0)*0.22;
  vec3 c = uColor*d + vec3(s);
  c = mix(c, vec3(0.90,0.52,0.36), uHi*0.55);
  frag = vec4(pow(c, vec3(0.86)), uAlpha);
}`;

function initGL(){
  const cv = $('#gl'); const gl = cv.getContext('webgl2', {antialias:true, alpha:false});
  if(!gl){ cv.parentNode.innerHTML='<div class="pad">WebGL2 is required for the 3D view.</div>'; return; }
  V.gl = gl; V.canvas = cv;
  const mk=(t,s)=>{const sh=gl.createShader(t);gl.shaderSource(sh,s);gl.compileShader(sh);
    if(!gl.getShaderParameter(sh,gl.COMPILE_STATUS))console.error(gl.getShaderInfoLog(sh));return sh;};
  const p = gl.createProgram();
  gl.attachShader(p,mk(gl.VERTEX_SHADER,VS)); gl.attachShader(p,mk(gl.FRAGMENT_SHADER,FS));
  gl.bindAttribLocation(p,0,'aPos'); gl.linkProgram(p); gl.useProgram(p);
  V.prog=p;
  V.u = {mvp:gl.getUniformLocation(p,'uMVP'), model:gl.getUniformLocation(p,'uModel'),
         color:gl.getUniformLocation(p,'uColor'), alpha:gl.getUniformLocation(p,'uAlpha'),
         hi:gl.getUniformLocation(p,'uHi')};

  DATA.scene.parts.forEach((pt,i)=>{
    const pos = decodeGeo(pt.geo);
    const vao = gl.createVertexArray(); gl.bindVertexArray(vao);
    const vb = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vb);
    gl.bufferData(gl.ARRAY_BUFFER, pos, gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0); gl.vertexAttribPointer(0,3,gl.FLOAT,false,0,0);
    gl.bindVertexArray(null);
    V.parts.push({...pt, vao, count: pt.geo.n, rgb: hex2rgb(pt.color)});
  });

  const c = DATA.scene.center, bb = DATA.scene.bbox;
  V.target = [c[0], c[1], c[2]];
  V.dist = Math.max(bb[1][0]-bb[0][0], bb[1][1]-bb[0][1]) * 1.5;
  gl.enable(gl.DEPTH_TEST); gl.clearColor(0.055,0.062,0.078,1);
  bindOrbit(cv);
  requestAnimationFrame(loop);
}

function mat(){ return new Float32Array(16); }
function ident(m){ m.fill(0); m[0]=m[5]=m[10]=m[15]=1; return m; }
function mul(o,a,b){ const r=mat();
  for(let i=0;i<4;i++)for(let j=0;j<4;j++){let s=0;for(let k=0;k<4;k++)s+=a[k*4+j]*b[i*4+k];r[i*4+j]=s;}
  o.set(r); return o; }
function persp(m,f,ar,n,fa){ ident(m); const t=1/Math.tan(f/2);
  m[0]=t/ar;m[5]=t;m[10]=(fa+n)/(n-fa);m[11]=-1;m[14]=2*fa*n/(n-fa);m[15]=0; return m; }
function lookAt(m,e,c,u){
  const z=norm(sub(e,c)), x=norm(cross(u,z)), y=cross(z,x); ident(m);
  m[0]=x[0];m[4]=x[1];m[8]=x[2]; m[1]=y[0];m[5]=y[1];m[9]=y[2]; m[2]=z[0];m[6]=z[1];m[10]=z[2];
  m[12]=-dot(x,e);m[13]=-dot(y,e);m[14]=-dot(z,e); return m; }
const sub=(a,b)=>[a[0]-b[0],a[1]-b[1],a[2]-b[2]];
const dot=(a,b)=>a[0]*b[0]+a[1]*b[1]+a[2]*b[2];
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const norm=a=>{const l=Math.hypot(...a)||1;return [a[0]/l,a[1]/l,a[2]/l];};
function trans(m,x,y,z){ ident(m); m[12]=x;m[13]=y;m[14]=z; return m; }

function resizeGL(){
  if(!V.gl) return;
  const cv=V.canvas, dpr=Math.min(devicePixelRatio||1, 2);
  const w=cv.clientWidth*dpr|0, h=cv.clientHeight*dpr|0;
  if(cv.width!==w||cv.height!==h){ cv.width=w; cv.height=h; }
}

function loop(){
  requestAnimationFrame(loop);
  if(current!=='model' || !V.gl) return;
  if(V.spin) V.az += 0.0042;
  resizeGL();
  const gl=V.gl; gl.viewport(0,0,V.canvas.width,V.canvas.height);
  gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);

  const ar = V.canvas.width/Math.max(1,V.canvas.height);
  const P = persp(mat(), 0.82, ar, 1, 4000);
  const ce=Math.cos(V.el), se=Math.sin(V.el);
  const eye=[V.target[0]+V.dist*ce*Math.sin(V.az), V.target[1]-V.dist*ce*Math.cos(V.az),
             V.target[2]+V.dist*se];
  const Vm = lookAt(mat(), eye, V.target, [0,0,1]);
  const VP = mul(mat(), P, Vm);
  gl.useProgram(V.prog);

  const t = V.explode/100;
  const draw = (p,i)=>{
    if(V.hidden.has(p.name)) return;
    const Mm = trans(mat(), p.explode[0]*t, p.explode[1]*t, p.explode[2]*t);
    gl.uniformMatrix4fv(V.u.model,false,Mm);
    gl.uniformMatrix4fv(V.u.mvp,false,mul(mat(),VP,Mm));
    gl.uniform3fv(V.u.color,p.rgb);
    gl.uniform1f(V.u.alpha,p.opacity);
    gl.uniform1f(V.u.hi, V.hover===i?1:0);
    gl.bindVertexArray(p.vao);
    gl.drawArrays(V.wire?gl.LINE_STRIP:gl.TRIANGLES, 0, p.count);
  };
  gl.disable(gl.BLEND); gl.depthMask(true);
  V.parts.forEach((p,i)=>{ if(p.opacity>=1) draw(p,i); });
  gl.enable(gl.BLEND); gl.blendFunc(gl.SRC_ALPHA,gl.ONE_MINUS_SRC_ALPHA); gl.depthMask(false);
  V.parts.forEach((p,i)=>{ if(p.opacity<1) draw(p,i); });
  gl.depthMask(true);
}

function bindOrbit(cv){
  let drag=null;
  cv.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY,b:e.button};cv.setPointerCapture(e.pointerId);});
  cv.addEventListener('pointerup',e=>{drag=null;});
  cv.addEventListener('pointermove',e=>{
    if(!drag) return;
    const dx=e.clientX-drag.x, dy=e.clientY-drag.y; drag.x=e.clientX; drag.y=e.clientY;
    if(drag.b===2||e.shiftKey){
      const s=V.dist*0.0016;
      V.target[0]-=dx*s*Math.cos(V.az); V.target[1]-=dx*s*Math.sin(V.az); V.target[2]+=dy*s;
    } else { V.az+=dx*0.0075; V.el=Math.max(-1.5,Math.min(1.5,V.el+dy*0.0075)); }
  });
  cv.addEventListener('contextmenu',e=>e.preventDefault());
  cv.addEventListener('wheel',e=>{e.preventDefault();
    V.dist=Math.max(40,Math.min(900,V.dist*(1+Math.sign(e.deltaY)*0.11)));},{passive:false});
}

const BUILD = {};
BUILD.model = pane => {
  pane.appendChild(document.getElementById('tpl-model').content.cloneNode(true));
  initGL();
  const list=$('#partlist',pane);
  V.parts.forEach((p,i)=>{
    const l=el('label','row');
    const cb=el('input'); cb.type='checkbox'; cb.checked=true;
    cb.onchange=()=>{ cb.checked?V.hidden.delete(p.name):V.hidden.add(p.name); };
    const sw=el('span','sw'); sw.style.background=p.color;
    const nm=el('span','nm',p.name);
    const ct=el('span','ct',fmt(p.tris));
    l.append(cb,sw,nm,ct);
    l.onmouseenter=()=>{V.hover=i; $('#hud',pane).textContent=p.name+' · '+fmt(p.tris)+' triangles';};
    l.onmouseleave=()=>{V.hover=-1; hud();};
    list.appendChild(l);
  });
  const groups=[...new Set(V.parts.map(p=>p.group))];
  const gl2=$('#grouplist',pane);
  groups.forEach(g=>{
    const l=el('label','row'); const cb=el('input'); cb.type='checkbox'; cb.checked=true;
    cb.onchange=()=>{ V.parts.forEach(p=>{ if(p.group===g){ cb.checked?V.hidden.delete(p.name):V.hidden.add(p.name);} });
      [...list.querySelectorAll('input')].forEach((x,i)=>{ if(V.parts[i].group===g) x.checked=cb.checked; }); };
    l.append(cb, el('span','nm',g)); gl2.appendChild(l);
  });
  $('#explode',pane).oninput=e=>{V.explode=+e.target.value; hud();};
  pane.querySelectorAll('[data-ex]').forEach(b=>b.onclick=()=>{
    V.explode=+b.dataset.ex; $('#explode',pane).value=V.explode; hud();});
  pane.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>{
    const v=b.dataset.view;
    if(v==='iso'){V.az=3.76;V.el=0.52;} if(v==='top'){V.az=Math.PI;V.el=1.5;}
    if(v==='front'){V.az=Math.PI;V.el=0.07;} if(v==='side'){V.az=Math.PI*1.5;V.el=0.07;}
  });
  $('#spin',pane).onclick=e=>{V.spin=!V.spin; e.target.style.color=V.spin?'var(--accent)':'';};
  $('#wire',pane).onclick=e=>{V.wire=!V.wire; e.target.style.color=V.wire?'var(--accent)':'';};
  function hud(){
    const s=DATA.cfg.stack, c=DATA.cfg.case;
    $('#hud',pane).textContent =
      `${c.width} x ${c.height} x ${s.bezel_top.toFixed(1)} mm case · ${s.knob_top.toFixed(1)} mm to the top of the dial`
      + (V.explode? ` · exploded ${V.explode}%`:'')
      + ' · drag to orbit, shift-drag to pan, scroll to zoom';
  }
  hud();
};
addEventListener('resize',()=>{resizeGL(); if(current==='pcb') drawPCB();});

/* ================= PCB viewer ================= */
const P = {view:{x:0,y:0,s:1}, flip:false, refs:false, net:'', layers:{
  pourB:true, pourF:true, trackB:true, trackF:true, pads:true, silk:true, drill:true, outline:true, touch:true}};
const LAYER_UI = [
  ['outline','Board outline','#7f8794'], ['pourB','GND pour (bottom)','#1f4d3a'],
  ['pourF','GND pour (top)','#5c3a2a'], ['trackB','Tracks (bottom)','#3fa06e'],
  ['trackF','Tracks (top)','#d97757'], ['pads','Pads & vias','#d9b25a'],
  ['touch','Touch pads','#c8b3ff'], ['drill','Drill holes','#0d0f13'],
  ['silk','Silkscreen','#e9eef5']
];
BUILD.pcb = pane => {
  pane.appendChild(document.getElementById('tpl-pcb').content.cloneNode(true));
  const ll=$('#layerlist',pane);
  LAYER_UI.forEach(([k,label,col])=>{
    const l=el('label','row'); const cb=el('input'); cb.type='checkbox'; cb.checked=P.layers[k];
    cb.onchange=()=>{P.layers[k]=cb.checked; drawPCB();};
    const sw=el('span','sw'); sw.style.background=col;
    l.append(cb,sw,el('span','nm',label)); ll.appendChild(l);
  });
  const b=DATA.board, st=b.stats;
  $('#pcbstats',pane).innerHTML =
    `${b.bounds[2]-b.bounds[0]} x ${b.bounds[3]-b.bounds[1]} mm, ${DATA.cfg.pcb.thickness} mm, 2 layers<br>`+
    `${fmt(st.components)} parts · ${fmt(st.pads)} pads · ${fmt(st.nets)} nets<br>`+
    `${fmt(st.tracks)} track segments · ${fmt(st.vias)} vias<br>`+
    `${fmt(st.connections)} of ${fmt(st.connections)} connections routed`;
  const sel=$('#netsel',pane);
  const nets=[...new Set(b.tracks.map(t=>t[6]))].sort();
  sel.appendChild(new Option('(none)',''));
  nets.forEach(n=>sel.appendChild(new Option(n,n)));
  sel.onchange=()=>{P.net=sel.value; drawPCB();};
  $('#pcbFit',pane).onclick=()=>{fitPCB(); drawPCB();};
  $('#pcbFlip',pane).onclick=()=>{P.flip=!P.flip; drawPCB();};
  $('#pcbRefs',pane).onchange=e=>{P.refs=e.target.checked; drawPCB();};
  const cv=$('#pcb',pane);
  let drag=null;
  cv.addEventListener('pointerdown',e=>{drag={x:e.clientX,y:e.clientY};cv.setPointerCapture(e.pointerId);});
  cv.addEventListener('pointerup',()=>drag=null);
  cv.addEventListener('pointermove',e=>{
    if(drag){ P.view.x+=e.clientX-drag.x; P.view.y+=e.clientY-drag.y; drag={x:e.clientX,y:e.clientY}; drawPCB(); }
    else hoverPCB(e);
  });
  cv.addEventListener('wheel',e=>{e.preventDefault();
    const r=cv.getBoundingClientRect(), mx=e.clientX-r.left, my=e.clientY-r.top;
    const k=e.deltaY<0?1.12:1/1.12;
    P.view.x=mx-(mx-P.view.x)*k; P.view.y=my-(my-P.view.y)*k; P.view.s*=k; drawPCB();},{passive:false});
  fitPCB();
};
function fitPCB(){
  const cv=$('#pcb'); if(!cv) return;
  const b=DATA.board.bounds, w=b[2]-b[0], h=b[3]-b[1];
  const s=Math.min(cv.clientWidth/(w*1.08), cv.clientHeight/(h*1.14));
  P.view.s=s;
  P.view.x=cv.clientWidth/2 - (b[0]+w/2)*s;
  P.view.y=cv.clientHeight/2 - (b[1]+h/2)*s;
}
function drawPCB(){
  const cv=$('#pcb'); if(!cv) return;
  const dpr=Math.min(devicePixelRatio||1,2);
  cv.width=cv.clientWidth*dpr; cv.height=cv.clientHeight*dpr;
  const g=cv.getContext('2d'); g.setTransform(dpr,0,0,dpr,0,0);
  g.fillStyle='#0d0f13'; g.fillRect(0,0,cv.clientWidth,cv.clientHeight);
  const b=DATA.board, L=P.layers, s=P.view.s;
  g.save();
  g.translate(P.view.x,P.view.y); g.scale(s,s);
  if(P.flip){ const cx=(b.bounds[0]+b.bounds[2])/2; g.translate(2*cx,0); g.scale(-1,1); }
  g.lineJoin='round'; g.lineCap='round';

  // substrate
  g.beginPath(); b.outline.forEach((p,i)=>i?g.lineTo(p[0],p[1]):g.moveTo(p[0],p[1])); g.closePath();
  g.fillStyle='#141a17'; g.fill();

  const hi = n => P.net && n===P.net;
  const alpha = n => (!P.net || hi(n)) ? 1 : 0.18;

  if(L.pourB){ g.fillStyle='#1c4436'; b.pours['B.Cu'].forEach(r=>g.fillRect(r[0],r[1],r[2]-r[0],r[3]-r[1])); }
  if(L.pourF){ g.globalAlpha=0.55; g.fillStyle='#5c3a2a';
    b.pours['F.Cu'].forEach(r=>g.fillRect(r[0],r[1],r[2]-r[0],r[3]-r[1])); g.globalAlpha=1; }

  const drawTracks=(layer,col)=>{
    b.tracks.forEach(t=>{ if(t[0]!==layer) return;
      g.globalAlpha=alpha(t[6]); g.strokeStyle=hi(t[6])?'#fff':col; g.lineWidth=t[5];
      g.beginPath(); g.moveTo(t[1],t[2]); g.lineTo(t[3],t[4]); g.stroke(); });
    g.globalAlpha=1;
  };
  if(L.trackB) drawTracks(1,'#3fa06e');
  if(L.trackF) drawTracks(0,'#d97757');

  if(L.touch){ g.fillStyle='#8f7ad6'; b.touch_pads.forEach(t=>g.fillRect(t[0],t[1],t[2]-t[0],t[3]-t[1])); }

  if(L.pads){
    b.pads.forEach(p=>{
      g.globalAlpha=alpha(p[7]);
      g.fillStyle = hi(p[7]) ? '#fff' : (p[0]===2?'#d9b25a':(p[0]===0?'#e08a63':'#57b98a'));
      if(p[5]==='c'){ g.beginPath(); g.arc(p[1],p[2],Math.max(p[3],p[4])/2,0,7); g.fill(); }
      else g.fillRect(p[1]-p[3]/2,p[2]-p[4]/2,p[3],p[4]);
    });
    g.globalAlpha=1;
    g.fillStyle='#c9a24a';
    b.vias.forEach(v=>{ g.beginPath(); g.arc(v[0],v[1],0.35,0,7); g.fill(); });
  }
  if(L.drill){
    g.fillStyle='#0d0f13';
    b.pads.forEach(p=>{ if(p[6]>0){ g.beginPath(); g.arc(p[1],p[2],p[6]/2,0,7); g.fill(); } });
    b.vias.forEach(v=>{ g.beginPath(); g.arc(v[0],v[1],0.175,0,7); g.fill(); });
    b.npth.forEach(h=>{ g.beginPath(); g.arc(h[0],h[1],h[2]/2,0,7); g.fill(); });
  }
  if(L.silk){
    g.strokeStyle='rgba(233,238,245,.5)'; g.lineWidth=0.14;
    b.components.forEach(c=>{ g.beginPath();
      c.outline.forEach((p,i)=>i?g.lineTo(p[0],p[1]):g.moveTo(p[0],p[1])); g.closePath(); g.stroke(); });
    g.fillStyle='rgba(233,238,245,.92)';
    b.logo.forEach(bl=>{ g.beginPath(); bl.forEach((p,i)=>i?g.lineTo(p[0],p[1]):g.moveTo(p[0],p[1]));
      g.closePath(); g.fill(); });
    if(P.refs && s>2.2){
      g.fillStyle='rgba(233,238,245,.8)'; g.font='1.2px ui-monospace,monospace'; g.textAlign='center';
      b.components.forEach(c=>g.fillText(c.ref,c.x,c.y-0.4));
    }
  }
  if(L.outline){
    g.strokeStyle='#8b93a1'; g.lineWidth=0.25;
    g.beginPath(); b.outline.forEach((p,i)=>i?g.lineTo(p[0],p[1]):g.moveTo(p[0],p[1]));
    g.closePath(); g.stroke();
  }
  g.restore();
  $('#pcbhud').textContent =
    `${b.bounds[2]-b.bounds[0]} x ${b.bounds[3]-b.bounds[1]} mm · ${P.flip?'viewed from the back':'viewed from the front'} · drag to pan, scroll to zoom`;
}
function hoverPCB(e){
  const cv=$('#pcb'), r=cv.getBoundingClientRect();
  let x=(e.clientX-r.left-P.view.x)/P.view.s, y=(e.clientY-r.top-P.view.y)/P.view.s;
  if(P.flip){ const cx=(DATA.board.bounds[0]+DATA.board.bounds[2])/2; x=2*cx-x; }
  let best=null,bd=1.4;
  DATA.board.pads.forEach(p=>{ const d=Math.hypot(p[1]-x,p[2]-y); if(d<bd){bd=d;best=p;} });
  const hud=$('#pcbhud');
  if(best) hud.textContent = `${best[8]}  net ${best[7]||'(none)'}  ${best[3]}x${best[4]} mm`;
  else hud.textContent = `${x.toFixed(1)}, ${y.toFixed(1)} mm`;
}

/* ================= static tabs ================= */
BUILD.schem = pane => {
  const d=el('div','pad'); d.style.maxWidth='none';
  d.innerHTML = '<h2>Schematic</h2><p>Functional schematic generated from the same netlist that '+
    'drives the PCB and the KiCad export - if it is on this sheet, it is on the board.</p>'+
    document.getElementById('schemsrc').innerHTML +
    '<div class="legend">'+
    '<span><i style="background:#d97757"></i>power</span>'+
    '<span><i style="background:#6ea8fe"></i>digital</span>'+
    '<span><i style="background:#3fb27f"></i>analog</span>'+
    '<span><i style="background:#c8b3ff"></i>touch</span></div>';
  pane.appendChild(d);
};
BUILD.bom = pane => {
  const d=el('div','pad');
  let total=0; DATA.bom.forEach(r=>total+=+r.Qty);
  d.innerHTML = `<h2>Bill of materials</h2>
    <div class="cards">
      <div class="card"><div class="k">Line items</div><div class="v">${DATA.bom.length}</div></div>
      <div class="card"><div class="k">Components</div><div class="v">${total}</div></div>
      <div class="card"><div class="k">Nets</div><div class="v">${DATA.board.stats.nets}</div></div>
      <div class="card"><div class="k">Board</div><div class="v small">${DATA.board.bounds[2]-DATA.board.bounds[0]} x ${DATA.board.bounds[3]-DATA.board.bounds[1]} mm, 2 layer</div></div>
    </div>`;
  const t=el('table');
  t.innerHTML='<thead><tr><th>Qty</th><th>Value</th><th>Footprint</th><th>Part / note</th><th>References</th></tr></thead>';
  const tb=el('tbody');
  DATA.bom.forEach(r=>{
    const tr=el('tr');
    tr.innerHTML=`<td class="num">${r.Qty}</td><td>${r.Value}</td><td class="mono">${r.Footprint}</td>
      <td>${r['MPN / Notes']}<div class="k2">${r.Description}</div></td>
      <td class="mono k2">${r.References}</td>`;
    tb.appendChild(tr);
  });
  t.appendChild(tb); d.appendChild(t); pane.appendChild(d);
};
BUILD.print = pane => {
  const d=el('div','pad'); const s=DATA.stl;
  d.innerHTML=`<h2>Printed parts</h2>
   <div class="cards">
     <div class="card"><div class="k">Pieces</div><div class="v">${s.totals.pieces_to_print}</div></div>
     <div class="card"><div class="k">Material</div><div class="v">${s.totals.estimated_filament_g} g</div></div>
     <div class="card"><div class="k">Solid volume</div><div class="v">${s.totals.solid_volume_cm3} cm3</div></div>
     <div class="card"><div class="k">All watertight</div><div class="v" style="color:var(--ok)">${s.ok?'yes':'no'}</div></div>
   </div>`;
  const t=el('table');
  t.innerHTML='<thead><tr><th>Part</th><th>Qty</th><th>Size (mm)</th><th>Material</th><th>Orientation</th><th>Mesh</th></tr></thead>';
  const tb=el('tbody');
  s.parts.forEach(p=>{
    const tr=el('tr');
    tr.innerHTML=`<td class="mono">${p.file}</td><td class="num">${p.qty}</td>
      <td class="num">${p.bbox_mm.join(' x ')}</td><td>${p.material}</td>
      <td>${p.orientation}<div class="k2">${p.notes}</div></td>
      <td class="num k2">${fmt(p.triangles)} tri<br>${p.volume_cm3} cm3</td>`;
    tb.appendChild(tr);
  });
  t.appendChild(tb); d.appendChild(t);
  d.insertAdjacentHTML('beforeend', ASSEMBLY_HTML());
  pane.appendChild(d);
};
BUILD.fw = pane => {
  const d=el('div','pad'); d.innerHTML=FIRMWARE_HTML(); pane.appendChild(d);
};
BUILD.verify = pane => {
  const d=el('div','pad'); const dr=DATA.drc, s=DATA.stl;
  const e=dr.errors, w=dr.warnings;
  d.innerHTML=`<h2>Verification</h2>
   <p>Every build runs these. The PCB ships only when the electrical, manufacturing and
      mechanical checks all pass, and every STL is proven to be a closed, correctly
      oriented 2-manifold before it is written.</p>
   <div class="cards">
     <div class="card"><div class="k">DRC / ERC errors</div><div class="v" style="color:${e?'var(--err)':'var(--ok)'}">${e}</div></div>
     <div class="card"><div class="k">Warnings</div><div class="v" style="color:${w?'var(--warn)':'var(--ok)'}">${w}</div></div>
     <div class="card"><div class="k">Connections routed</div><div class="v">${DATA.board.stats.connections}/${DATA.board.stats.connections}</div></div>
     <div class="card"><div class="k">Meshes watertight</div><div class="v" style="color:var(--ok)">${s.parts.length}/${s.parts.length}</div></div>
   </div>
   <h2>Board checks</h2>`;
  dr.findings.forEach(f=>{
    const r=el('div','finding');
    r.innerHTML=`<span class="tag ${f.severity}">${f.severity}</span>
      <span><span class="mono k2">${f.check}</span> &nbsp;${f.message}</span>`;
    d.appendChild(r);
  });
  d.insertAdjacentHTML('beforeend','<h2>Mesh checks</h2>');
  const t=el('table');
  t.innerHTML='<thead><tr><th>Part</th><th>Triangles</th><th>Watertight</th><th>Manifold</th><th>Oriented</th><th>Volume</th></tr></thead>';
  const tb=el('tbody');
  s.parts.forEach(p=>{
    const yes=v=>`<span style="color:${v?'var(--ok)':'var(--err)'}">${v?'pass':'FAIL'}</span>`;
    const tr=el('tr');
    tr.innerHTML=`<td class="mono">${p.name}</td><td class="num">${fmt(p.triangles)}</td>
      <td>${yes(p.watertight)}</td><td>${yes(p.manifold)}</td><td>${yes(p.oriented)}</td>
      <td class="num">${p.volume_cm3} cm3</td>`;
    tb.appendChild(tr);
  });
  t.appendChild(tb); d.appendChild(t);
  pane.appendChild(d);
};

function ASSEMBLY_HTML(){
  const c=DATA.cfg;
  return `<h2>Assembly</h2>
  <ol class="steps">
    <li><b>Print.</b> ${c.case.print.layer_height} mm layers, ${c.case.print.walls} walls,
        ${c.case.print.infill_pct}% infill, no supports on any part. Print the six agent
        keycaps in natural or clear filament so the status colour reads through the
        1.0 mm roof.</li>
    <li><b>Populate the board.</b> Bottom side first: diodes, the 0603 passives, the
        SOT-23-5 buffer, then the RP2040 module. Top side next: the 19 SK6812s, then
        the 13 Choc switches, the EC11 and the thumbstick.</li>
    <li><b>Heat-set inserts.</b> Press six M2 brass inserts into the bosses on the
        underside of the bezel, ${c.case.screw.insert_h} mm deep.</li>
    <li><b>Flash.</b> Hold BOOTSEL on the module, plug in USB, drag the UF2 across.
        The board enumerates as <span class="mono">${c.meta.usb.product}</span>.</li>
    <li><b>Smoke test.</b> Run the built-in self test (hold LAYER on power-up): it walks
        the LED chain, prints every matrix intersection, and streams the two ADC axes
        and the five touch channels.</li>
    <li><b>Close it up.</b> Drop the diffuser into the touch window, seat the PCB on the
        tray standoffs, fit the bezel and drive six M2 x 6 mm screws from underneath.</li>
    <li><b>Caps and feet.</b> Press the keycaps on, the dial onto the flatted shaft, the
        thumb cap onto the stick, and stick the four feet on for the
        ${c.case.foot.tilt_deg}° typing angle.</li>
  </ol>`;
}
function FIRMWARE_HTML(){
  const c=DATA.cfg;
  const rows = c.layers.map(l=>`<tr><td class="num">${l.n}</td><td class="mono">${l.name}</td><td>${l.desc}</td></tr>`).join('');
  const sc = c.rgb.status_colors;
  const chips = Object.entries(sc).map(([k,v])=>
    `<span class="pill" style="color:rgb(${v.join(',')})"><span class="sw" style="display:inline-block;background:rgb(${v.join(',')});margin-right:6px;vertical-align:-1px"></span>${k}</span>`).join(' ');
  return `<h2>Firmware</h2>
  <p>QMK for RP2040. The agent keys are driven by a small host-side bridge that pushes
     thread status over raw HID; everything else is stock QMK plus a custom matrix scan
     for the RC touch strip.</p>
  <div class="cards">
    <div class="card"><div class="k">MCU</div><div class="v small">${c.pins.mcu}</div></div>
    <div class="card"><div class="k">Matrix</div><div class="v small">${c.matrix.rows} x ${c.matrix.cols}, ${c.matrix.diode_direction}</div></div>
    <div class="card"><div class="k">RGB</div><div class="v small">${c.rgb.total} x SK6812, ${c.rgb.firmware_current_limit_ma} mA cap</div></div>
    <div class="card"><div class="k">Layers</div><div class="v small">${c.layers.length} programmable</div></div>
  </div>
  <h2>Agent status colours</h2><div style="display:flex;gap:8px;flex-wrap:wrap">${chips}</div>
  <h2>Layers</h2>
  <table><thead><tr><th>#</th><th>Name</th><th>Purpose</th></tr></thead><tbody>${rows}</tbody></table>
  <h2>Pin map</h2>
  <pre>ROW0..3        ${c.matrix.row_pins.join(', ')}
COL0..3        ${c.matrix.col_pins.join(', ')}
Encoder A/B    ${c.pins.encoder_a}, ${c.pins.encoder_b}
LED data       ${c.pins.led_data}  (through a 74LVC1G17 to 5 V)
Touch drive    ${c.pins.touch_drive}
Touch sense    ${c.pins.touch_sense.join(', ')}
Joystick X/Y   ${c.pins.joy_x}, ${c.pins.joy_y}  (ADC0, ADC1)
Joystick click GP22
Reset          ${c.pins.reset}</pre>
  <p>Build with <span class="mono">qmk compile -kb claude_micro -km default</span>. The
     firmware directory in the repository is a drop-in QMK keyboard folder.</p>`;
}

/* fill the pills and open the first tab */
document.getElementById('pillGeom').textContent =
  DATA.stl.totals.pieces_to_print + ' printed parts · ' + DATA.stl.totals.estimated_filament_g + ' g';
document.getElementById('pillPcb').textContent =
  DATA.board.stats.components + ' parts · ' + DATA.board.stats.tracks + ' segments · ' + DATA.board.stats.vias + ' vias';
const p3=document.getElementById('pillDrc');
p3.textContent = DATA.drc.errors===0 ? 'DRC + ERC clean' : DATA.drc.errors+' DRC errors';
if(DATA.drc.errors===0) p3.classList.add('ok');
show('model');
</script>
<div id="schemsrc" style="display:none"><!--__SCHEMATIC__--></div>
</body></html>
"""


if __name__ == "__main__":
    build()
