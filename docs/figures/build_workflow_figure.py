"""Generate the implementation-grounded paper figure as SVG and editable draw.io.

Run with fastapi_env Python. Uses installed Windows fonts and fontTools; no network.
"""
from pathlib import Path
from html import escape
import json
import xml.etree.ElementTree as ET
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen

OUT = Path(__file__).resolve().parent
WIDTH, HEIGHT = 1640, 2230
INK, MUTED, LINE = "#252525", "#626262", "#555555"
fonts = {
    "normal": TTFont("C:/Windows/Fonts/msyh.ttc", fontNumber=0),
    "bold": TTFont("C:/Windows/Fonts/msyhbd.ttc", fontNumber=0),
    "code": TTFont("C:/Windows/Fonts/consola.ttf"),
}
glyph_defs, text_runs, shapes, arrows, nodes = {}, [], [], [], []
mx = ET.Element("mxfile", host="app.diagrams.net", version="24.7.17")
diagram = ET.SubElement(mx, "diagram", id="rca-rag-current", name="已实现工作流")
model = ET.SubElement(diagram, "mxGraphModel", dx=str(WIDTH), dy=str(HEIGHT), grid="1", gridSize="10",
    guides="1", tooltips="1", connect="1", arrows="1", fold="1", page="1", pageScale="1",
    pageWidth=str(WIDTH), pageHeight=str(HEIGHT), math="0", shadow="0")
root = ET.SubElement(model, "root")
ET.SubElement(root, "mxCell", id="0")
ET.SubElement(root, "mxCell", id="1", parent="0")
serial = 1


def ident(prefix):
    global serial
    serial += 1
    return prefix + str(serial)


def vertex(x, y, w, h, style, value="", parent="1"):
    key = ident("v")
    cell = ET.SubElement(root, "mxCell", id=key, value=value, style=style, vertex="1", parent=parent)
    ET.SubElement(cell, "mxGeometry", x=str(x), y=str(y), width=str(w), height=str(h), attrib={"as": "geometry"})
    return key


def measure(text, size, font):
    f = fonts[font]
    return sum(f["hmtx"].metrics[f.getBestCmap().get(ord(c), ".notdef")][0] for c in text) * size / f["head"].unitsPerEm


def text(x, baseline, value, size=23, font="normal", color=INK, anchor="start", max_width=None, parent="1"):
    width = measure(value, size, font)
    if max_width and width > max_width + .1:
        raise ValueError(f"Text overflows ({width:.1f}>{max_width}): {value}")
    left = x - (width / 2 if anchor == "middle" else width if anchor == "end" else 0)
    text_runs.append(dict(x=left, y=baseline, text=value, size=size, font=font, color=color, width=width))
    family = "Consolas" if font == "code" else "Microsoft YaHei"
    # Draw.io coordinates for group children are supplied separately by node().
    return dict(x=left, y=baseline - size * 1.05, w=width+3, h=size*1.4, value=value,
        style=f"text;html=0;strokeColor=none;fillColor=none;align=left;verticalAlign=middle;whiteSpace=wrap;rounded=0;spacing=0;fontFamily={family};fontSize={size};fontColor={color};fontStyle={1 if font=='bold' else 0};")


def label(x, y, value, size=22, font="normal", color=MUTED, anchor="start", max_width=None):
    t = text(x,y,value,size,font,color,anchor,max_width)
    vertex(t["x"],t["y"],t["w"],t["h"],t["style"],value)


def panel(x, y, w, h, name):
    shapes.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="#fafafa" stroke="#bcbcbc" stroke-width="1.3"/>')
    vertex(x,y,w,h,"rounded=0;whiteSpace=wrap;html=0;fillColor=#fafafa;strokeColor=#bcbcbc;strokeWidth=1.3;")
    label(x+23,y+37,name,26,"bold",INK)


def node(key,x,y,w,h,title,lines,kind="process"):
    fill = "#f1f1f1" if kind == "store" else "#ffffff"
    if kind == "store":
        d=f'M{x},{y+13} C{x},{y-4} {x+w},{y-4} {x+w},{y+13} L{x+w},{y+h-13} C{x+w},{y+h+4} {x},{y+h+4} {x},{y+h-13} Z M{x},{y+13} C{x},{y+30} {x+w},{y+30} {x+w},{y+13}'
        shape=f'<path d="{d}" fill="{fill}" stroke="{LINE}" stroke-width="1.6"/>'
        style="shape=cylinder3;boundedLbl=1;backgroundOutline=1;size=13;"
    else:
        shape=f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="3" fill="{fill}" stroke="{LINE}" stroke-width="1.6"/>'
        style="rounded=0;"
    shapes.append(shape)
    group=vertex(x,y,w,h,"group;")
    vertex(0,0,w,h,style+f"html=0;fillColor={fill};strokeColor={LINE};strokeWidth=1.6;",parent=group)
    baseline=y+(46 if kind=="store" else 32)
    ts=[text(x+w/2,baseline,title,25,"bold",INK,"middle",w-22)]
    for line in lines:
        if isinstance(line,str): value,font,size=line,"normal",22
        else: value,font,size=line
        baseline+=26 if kind=="store" else 27
        ts.append(text(x+w/2,baseline,value,size,font,MUTED if font!="code" else INK,"middle",w-22))
    if baseline > y+h-10: raise ValueError(f"Node height overflow: {key} {baseline} > {y+h-10}")
    for t in ts:
        vertex(t["x"]-x,t["y"]-y,t["w"],t["h"],t["style"],t["value"],parent=group)
    nodes.append(dict(id=key,x=x,y=y,w=w,h=h,title=title))


def edge(points, dashed=False, both=False, note=None):
    coords=" ".join(f"{x},{y}" for x,y in points)
    arrows.append(f'<polyline points="{coords}" fill="none" stroke="{LINE}" stroke-width="1.7" stroke-linejoin="round" marker-end="url(#arrow)"'+(' stroke-dasharray="7 5"' if dashed else '')+(' marker-start="url(#arrow-start)"' if both else '')+'/>')
    cell=ET.SubElement(root,"mxCell",id=ident("e"),value="",style="edgeStyle=none;rounded=0;html=0;endArrow=block;endFill=1;strokeColor=#555555;strokeWidth=1.7;"+("dashed=1;dashPattern=7 5;" if dashed else "")+("startArrow=block;startFill=1;" if both else ""),edge="1",parent="1")
    geo=ET.SubElement(cell,"mxGeometry",relative="1",attrib={"as":"geometry"})
    ET.SubElement(geo,"mxPoint",x=str(points[0][0]),y=str(points[0][1]),attrib={"as":"sourcePoint"})
    ET.SubElement(geo,"mxPoint",x=str(points[-1][0]),y=str(points[-1][1]),attrib={"as":"targetPoint"})
    if len(points)>2:
        arr=ET.SubElement(geo,"Array",attrib={"as":"points"})
        for x,y in points[1:-1]: ET.SubElement(arr,"mxPoint",x=str(x),y=str(y))
    if note: label(*note)


label(40,44,"RCA–RAG 诊断与工单工作流",33,"bold",INK)
label(1600,42,"当前实现 · 2026-09",22,"normal",MUTED,"end")
panel(40,85,740,600,"(a) 时序分析与事件契约")
panel(820,85,780,600,"(b) 三域知识库构建")
panel(40,725,1560,640,"(c) 事件约束检索与证据融合")
panel(40,1430,1560,485,"(d) 工单生成、引用校验与人工复核")
panel(40,1970,1560,170,"(e) 本地控制与运行审计")

# (a) CSV -> detection -> independent RCA / statistics -> bundle -> incident.
node("csv",65,155,275,102,"原始时序 CSV",["RE1 / 自有数值指标",("time + metrics","code",22)])
node("detect",395,155,360,102,"校验与异常检测",["MAD 回放 / 人工触发", "参考窗 + 观测窗"])
node("rca",65,300,275,132,"根因候选分析",[("run_rca()","code",23),"BARO / ε-Diagnosis","候选排名；可关闭"])
node("metrics",395,300,360,132,"独立指标摘要",[("MetricSummary v1","code",23),"均值、变化量、窗口统计","不依赖 RCA 候选选择"])
node("bundle",65,480,275,132,"跨项目交换文件",[("signal-bundle.json","code",22),("SignalEnvelope v1","code",22),"检测 + 候选 + 摘要"])
node("incident",395,480,360,152,"事件与指标契约转换",[("IncidentDocument v1.1","code",22),"统一故障事件 + 独立指标摘要",("convert_signal_bundle()","code",21),("incident.json","code",21)])
edge([(340,206),(395,206)])
edge([(505,257),(505,278),(202,278),(202,300)])
edge([(645,257),(645,300)])
edge([(202,432),(202,480)])
edge([(575,432),(575,457),(290,457),(290,480)])
edge([(340,546),(395,546)])
label(62,668,"契约校验：隔离评测标签；未形成完整事件时不进入 RAG",21,max_width=704)

# (b) Versioned authoritative JSONL corpus; metrics join independently.
node("runbooks",845,155,330,108,"运维手册",["UTF-8 TXT / 目录", "适用说明与处置知识"])
node("cases",1215,155,360,108,"已确认历史案例",[("HistoricalCase v1","code",23),"确认根因、依据与时间"])
node("knowledge",895,320,630,139,"统一知识文档",[("KnowledgeDocument v1","code",24),("build_knowledge_corpus()","code",22),("type = runbook | case | metric","code",22)])
node("manifest",895,515,630,105,"权威语料 · JSONL",[("corpus/manifest.jsonl","code",25)],kind="store")
edge([(1010,263),(1010,295),(1050,295),(1050,320)])
edge([(1395,263),(1395,295),(1370,295),(1370,320)])
edge([(755,546),(799,546),(799,410),(895,410)],note=(847,397,"摘要",21,"normal",MUTED,"middle"))
edge([(1210,459),(1210,515)])
label(1210,663,"每行一个对象：正文、类型、来源、raw_ref、metadata",21,anchor="middle",max_width=733)

# (c) Two inputs converge BEFORE embedding and route top-k.
node("query",80,810,550,108,"事件查询上下文",[("build_query_context()","code",24),"事件 + 用户问题 + 可选 RCA 扩展"])
node("chunks",925,810,605,108,"知识读取与分块",[("read_data / KnowledgeAwareParser","code",22),"手册 / 案例分块；指标摘要保持完整"])
edge([(755,546),(799,546),(799,690),(690,690),(690,785),(355,785),(355,810)])
edge([(1525,568),(1580,568),(1580,785),(1227,785),(1227,810)])
label(742,714,"当前事件",21,"normal",MUTED,"middle")
label(1547,699,"事件专属语料快照",21,"normal",MUTED,"end")
node("eligibility",80,983,375,154,"证据准入过滤",[("eligible()","code",24),"系统 / 当前事件 / 时间窗口","排除自身案例与未来证据","过滤先于编码与 Top-K"])
node("retrieval",510,983,550,154,"三域多路检索",[("OperationsRunner","code",25),("Doc  /  Case  /  Metric","code",23),"正文 + 可选知识路径","BM25 / Dense / Hybrid"])
node("fusion",1115,983,415,154,"融合、重排与打包",[("fuse_evidence()","code",23),"加权 RRF / 轮询合并","可选 gte-rerank-v2","去重 + Top-K + 字符预算"])
edge([(355,918),(355,948),(267,948),(267,983)])
edge([(1227,918),(1227,963),(402,963),(402,983)])
edge([(455,1060),(510,1060)])
edge([(1060,1060),(1115,1060)])
node("experiment",80,1190,375,128,"实验配置与追溯",[("ExperimentConfig","code",23),"来源 / 查询 / 融合开关","配置、语料与代码指纹"])
edge([(267,1190),(267,1137)],dashed=True)
node("cache",510,1182,550,144,"可选向量缓存 · SQLite",[("CachedEmbeddings","code",24),"text-embedding-v4 → 归一化向量","磁盘缓存；内存精确余弦检索"],kind="store")
edge([(785,1137),(785,1182)],dashed=True,both=True)
node("pack",1115,1190,415,128,"最终证据包",[("EvidencePackRecord v1","code",22),"EvidenceItem + 排名贡献","正文、引用 ID 与来源"])
edge([(1322,1137),(1322,1190)])
label(80,1346,"检索留痕：outputs/operations/run-*.json（查询、配置、各路得分、最终证据与诊断）",22,max_width=1450)

# (d) Generation -> validation -> immutable draft and reviewed revisions.
node("generate",80,1515,450,152,"证据约束生成",[("WorkOrderGenerator","code",24),"离线摘录 / GLM / Qwen","摘要、待验证候选、验证步骤","处置建议、前提、风险与回退"])
node("validate",585,1515,405,152,"结构与引用校验",[("parse_model_draft()","code",22),("check_citations()","code",22),"来源 ID / 原文引文 / 范围","可选一次草稿修复"])
node("workorder",1045,1515,485,152,"待复核工单",[("WorkOrderDraft","code",24),("WorkOrderRecord v1","code",24),"根因未确认；处置未执行","保留证据、配置与生成审计"])
edge([(1322,1318),(1322,1392),(690,1392),(690,1490),(305,1490),(305,1515)])
label(1050,1380,"最终上下文与引用",21,"normal",MUTED,"middle")
edge([(530,1591),(585,1591)])
edge([(990,1591),(1045,1591)])
node("genfiles",80,1745,450,141,"生成记录 · JSON / Markdown",[("outputs/work_orders/gen-*/","code",21),("work-order.{json,md} / audit.jsonl","code",20),("evidence-pack.json / prompt.json*","code",20)])
node("reviewfiles",585,1745,405,141,"独立人工修订版本",[("outputs/console/reviews/","code",20),("gen-*/rev-*/review.{json,md}","code",20),"新增版本，不覆盖原始草稿"])
node("review",1045,1745,485,141,"逐条人工复核",[("ReviewRequest / ClaimReview","code",22),"修改陈述 + 标注证据支持程度","仍需修订 / 已复核草稿"])
edge([(1160,1667),(1160,1706),(305,1706),(305,1745)])
edge([(1405,1667),(1405,1745)])
edge([(1045,1815),(990,1815)])
label(61,1946,"* prompt.json 仅在成功云生成时保存；引用检查不等于语义正确，复核不会自动确认根因或回流案例。",21,max_width=1515)

# (e) A separate control plane avoids suggesting UI settings are model evidence.
label(80,2051,"控制台与任务编排",24,"bold",INK)
label(80,2085,"Vue / FastAPI · JobQueue · Catalog",22,max_width=482)
label(80,2117,"任务快照：jobs/job-*/job.json",21,max_width=482)
label(585,2051,"模型与服务配置",24,"bold",INK)
label(585,2085,"ServiceSettings · GenerationConfig",21,max_width=410)
label(585,2117,"service-settings.json · DPAPI 密文",20,max_width=410)
label(1080,2051,"状态与错误日志",24,"bold",INK)
label(1080,2085,"Journal · 失败阶段 / 原因 / 建议",22,max_width=485)
label(1080,2117,"outputs/console/logs.json",22,"code",max_width=485)
label(40,2175,"图例：实线为数据流；虚线为实验配置或可选向量服务。RCA 与 EasyRAG 通过 JSON 契约独立运行。",21,max_width=1550)
label(40,2207,"示例手册与案例含教学样例；指标摘要来自 RE1 时序数据。日志、拓扑、图像检索及自动案例回流尚未接入。",21,max_width=1550)


def outlined(run):
    f=fonts[run["font"]]
    gs=f.getGlyphSet(); cmap=f.getBestCmap(); scale=run["size"]/f["head"].unitsPerEm
    cursor=run["x"]; parts=[]
    for ch in run["text"]:
        name=cmap.get(ord(ch), ".notdef")
        if name==".notdef": raise ValueError("Missing glyph: "+ch)
        gid="g-"+run["font"]+"-"+str(ord(ch))
        if gid not in glyph_defs:
            pen=SVGPathPen(gs); gs[name].draw(pen)
            glyph_defs[gid]=pen.getCommands()
        parts.append(f'<use href="#{gid}" xlink:href="#{gid}" transform="translate({cursor:.3f} {run["y"]}) scale({scale:.7f} {-scale:.7f})"/>')
        cursor+=f["hmtx"].metrics[name][0]*scale
    return '<g fill="'+run["color"]+'" aria-label="'+escape(run["text"],quote=True)+'"><title>'+escape(run["text"])+"</title>"+"".join(parts)+"</g>"


header=f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-labelledby="figure-title figure-desc">'
meta='<title id="figure-title">RCA–RAG 诊断与工单工作流：当前已实现部分</title><desc id="figure-desc">时序分析和三域知识转换，经统一事件与 JSONL manifest 构建上下文，完成准入过滤、多路检索、融合打包、工单生成、引用校验及人工复核。文件与 SQLite 为持久存储，控制台提供独立设置和脱敏运行日志。</desc>'
markers='<marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M0 0L8 4L0 8Z" fill="#555555"/></marker><marker id="arrow-start" markerWidth="8" markerHeight="8" refX="1" refY="4" orient="auto" markerUnits="userSpaceOnUse"><path d="M8 0L0 4L8 8Z" fill="#555555"/></marker>'
base='<rect width="100%" height="100%" fill="white"/>'+"".join(shapes)+"".join(arrows)
native=[]
for r in text_runs:
    family="Consolas,monospace" if r["font"]=="code" else "Microsoft YaHei,Noto Sans CJK SC,sans-serif"
    native.append(f'<text x="{r["x"]:.3f}" y="{r["y"]}" font-family="{family}" font-size="{r["size"]}" font-weight="{700 if r["font"]=="bold" else 400}" fill="{r["color"]}">{escape(r["text"])}</text>')
native_svg=header+meta+'<defs>'+markers+'</defs>'+base+"".join(native)+'</svg>'
path_text="".join(outlined(r) for r in text_runs)
defs="".join(f'<path id="{key}" d="{value}"/>' for key,value in glyph_defs.items())
path_svg=header+meta+'<defs>'+markers+defs+'</defs>'+base+path_text+'</svg>'
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'rca-rag-workflow.svg').write_text(path_svg,encoding='utf-8')
(OUT/'rca-rag-workflow-editable.svg').write_text(native_svg,encoding='utf-8')
ET.indent(mx,space="  ")
ET.ElementTree(mx).write(OUT/'rca-rag-workflow.drawio',encoding='utf-8',xml_declaration=True)
(OUT/'rca-rag-workflow-layout.json').write_text(json.dumps({'width':WIDTH,'height':HEIGHT,'nodes':nodes,'text_count':len(text_runs)},ensure_ascii=False,indent=2),encoding='utf-8')
for filename in ['rca-rag-workflow.svg','rca-rag-workflow-editable.svg','rca-rag-workflow.drawio']:
    ET.parse(OUT/filename)
print(f'Generated {len(nodes)} nodes, {len(text_runs)} text lines, {len(glyph_defs)} reusable vector glyphs.')
