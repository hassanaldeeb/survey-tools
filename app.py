"""Survey tools. DWG needs ODA File Converter (free): set ODA_PATH, on Linux run under xvfb-run."""
import io, os, re, shutil, subprocess, tempfile, zipfile
from flask import Flask, request, send_file, render_template_string, abort
from markupsafe import escape
import ezdxf
from pyproj import CRS, Transformer

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
ODA = os.environ.get("ODA_PATH", "ODAFileConverter")

_T = "+proj=tmerc +lat_0=30 +lon_0=%s +k=1 +x_0=%s +y_0=%s +a=6378200 +rf=298.3 +towgs84=-130,110,-13 +units=m +no_defs"
BELTS = {"red": _T % (31, 615000, 810000), "purple": _T % (27, 700000, 200000), "blue": _T % (35, 300000, 1100000),
         "sred": _T % (31, 615000, 1810000), "spur": _T % (27, 700000, 1200000)}

PAGE = """<!doctype html><html lang=ar dir=rtl><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>أدوات المساحة</title><style>body{font-family:system-ui,sans-serif;background:#eef0e6;color:#1d3557;max-width:640px;margin:auto;padding:14px}
form{background:#fff;border:1px solid #c9ccbd;padding:14px;margin-bottom:14px}label{display:block;margin:8px 0 3px;font-size:.9rem}
input,select,button{width:100%;padding:10px;box-sizing:border-box;font:inherit}button{background:#e85d04;color:#fff;border:0;margin-top:10px;font-weight:700}
.r{display:grid;grid-template-columns:1fr 1fr;gap:8px}</style><h1>أدوات المساحة</h1>
{% macro sysbox() %}<label>النظام</label><select name=system><option value=old>ETM (OLDEGYP)</option><option value=utm>WGS84 / UTM</option></select>
<div class=r><div><label>الحزام (OLDEGYP)</label><select name=belt><option value=red>ETM-1 Red</option><option value=purple>ETM-2 Purple</option><option value=blue>ETM-3 Blue</option><option value=sred>ETM-4 South Red</option><option value=spur>ETM-5 South Purple</option></select></div>
<div><label>الزون (UTM)</label><select name=zone><option>36</option><option>35</option><option>34</option><option>37</option></select></div></div>
{% endmacro %}
<form action=/convert_cad method=post enctype=multipart/form-data><b>1. TXT / CSV / SDR إلى DXF أو DWG</b>
<input type=file name=file required><label>ترتيب الأعمدة</label><select name=order><option value=nezc>N E Z Code</option><option value=pnezc>رقم N E Z Code</option><option value=enzc>E N Z Code</option><option value=penzc>رقم E N Z Code</option></select>
<label>الصيغة</label><select name=fmt><option value=dxf>DXF</option><option value=dwg>DWG (يحتاج ODA)</option></select><button>تحميل</button></form>
<form action=/dwg_to_dxf method=post enctype=multipart/form-data><b>DWG إلى DXF</b><input type=file name=cad required><button>تحميل DXF</button></form>
<form action=/to_kml method=post enctype=multipart/form-data><b>2. DWG / DXF إلى KML أو KMZ</b>
<input type=file name=cad required><label>الصيغة</label><select name=fmt><option value=kml>KML</option><option value=kmz>KMZ</option></select>{{ sysbox() }}<button>تحميل</button></form>
<form action=/transform_coords method=post><b>3. توقيع نقطة</b><div class=r><div><label>North</label><input type=number step=any name=north required></div>
<div><label>East</label><input type=number step=any name=east required></div></div>{{ sysbox() }}<button>احسب</button></form></html>"""


def tf(f):
    s = f.get("system", "old")
    try:
        if s == "utm":
            src = CRS.from_epsg(32600 + int(f.get("zone", 36)))
        else:
            src = CRS.from_proj4(BELTS[f.get("belt", "red")])
    except Exception:
        abort(400, "إعدادات نظام الإحداثيات غير صحيحة")
    return Transformer.from_crs(src, "EPSG:4326", always_xy=True)


def oda(data, src_ext, out_fmt):
    """Convert DWG<->DXF with ODA File Converter; returns bytes."""
    d = tempfile.mkdtemp()
    try:
        i, o = os.path.join(d, "i"), os.path.join(d, "o"); os.mkdir(i); os.mkdir(o)
        open(os.path.join(i, "f." + src_ext), "wb").write(data)
        subprocess.run([ODA, i, o, "ACAD2018", out_fmt.upper(), "0", "1"], check=True, timeout=180,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return open(os.path.join(o, "f." + out_fmt.lower()), "rb").read()
    except Exception:
        abort(500, "تعذر تشغيل ODA File Converter. تأكد من تثبيته وضبط ODA_PATH.")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def dwg_to_dxf(data):
    """LibreDWG's dwg2dxf if installed, else ODA File Converter."""
    exe = shutil.which("dwg2dxf")
    if not exe:
        return oda(data, "dwg", "dxf")
    d = tempfile.mkdtemp()
    try:
        i, o = os.path.join(d, "f.dwg"), os.path.join(d, "f.dxf")
        open(i, "wb").write(data)
        subprocess.run([exe, "-y", "-o", o, i], timeout=180, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not os.path.exists(o):
            abort(500, "فشل تحويل DWG")
        return open(o, "rb").read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


@app.after_request
def cors(r):
    r.headers["Access-Control-Allow-Origin"] = "*"
    return r


@app.route("/dwg_to_dxf", methods=["POST"])
def dwg_route():
    f = request.files.get("cad")
    if not f: abort(400, "ارفع ملف DWG")
    return send_file(io.BytesIO(dwg_to_dxf(f.read())), as_attachment=True, download_name="converted.dxf", mimetype="text/plain")


def parse_points(text, order):
    """order: nezc | pnezc | enzc | penzc (p = leading point number)."""
    pts = []
    for line in text.splitlines():
        s = line.strip()
        o, name = order, None
        if s.startswith("08"):
            t = s.split(); name, nums, o = (t[0][4:] or t[0]), t[1:], "nezc"
        else:
            t = [x for x in re.split(r"[,\s;]+", s) if x]
            if not t: continue
            if o.startswith("p"): name = t.pop(0)
            nums = t
        try:
            a, b = float(nums[0]), float(nums[1]); z = float(nums[2]) if len(nums) > 2 else 0.0
        except (ValueError, IndexError):
            continue
        n, e = (b, a) if o.lstrip("p").startswith("e") else (a, b)
        pts.append((name or str(len(pts) + 1), e, n, z, " ".join(nums[3:])))
    return pts


@app.route("/")
def index():
    return render_template_string(PAGE)


@app.route("/convert_cad", methods=["POST"])
def convert_cad():
    f = request.files.get("file")
    if not f: abort(400, "ارفع ملف")
    pts = parse_points(f.read().decode("utf-8", "ignore"), request.form.get("order", "nezc"))
    if not pts: abort(400, "لا توجد نقاط صالحة")
    doc = ezdxf.new("R2010"); doc.layers.add("POINTS"); doc.layers.add("NAMES"); msp = doc.modelspace()
    doc.layers.add("CODES")
    for name, e, n, z, code in pts:
        msp.add_point((e, n, z), dxfattribs={"layer": "POINTS"})
        msp.add_text(name, height=0.8, dxfattribs={"layer": "NAMES", "insert": (e + 0.3, n + 0.3)})
        if code: msp.add_text(code, height=0.6, dxfattribs={"layer": "CODES", "insert": (e + 0.3, n - 0.8)})
    buf = io.StringIO(); doc.write(buf); data = buf.getvalue().encode()
    ext = "dxf"
    if request.form.get("fmt") == "dwg":
        data, ext = oda(data, "dxf", "dwg"), "dwg"
    return send_file(io.BytesIO(data), as_attachment=True, download_name="survey_points." + ext)


@app.route("/to_kml", methods=["POST"])
def to_kml():
    f = request.files.get("cad")
    if not f: abort(400, "ارفع ملف")
    raw = f.read(); t = tf(request.form)
    if f.filename.lower().endswith(".dwg"):
        raw = dwg_to_dxf(raw)
    try:
        msp = ezdxf.read(io.StringIO(raw.decode("utf-8", "ignore"))).modelspace()
    except Exception:
        abort(400, "تعذر قراءة الملف")
    ll = lambda x, y: "%.8f,%.8f,0" % t.transform(x, y)
    out = []
    for p in msp.query("POINT"):
        out.append("<Placemark><Point><coordinates>%s</coordinates></Point></Placemark>" % ll(p.dxf.location.x, p.dxf.location.y))
    for l in msp.query("LINE"):
        out.append("<Placemark><LineString><coordinates>%s %s</coordinates></LineString></Placemark>" % (ll(l.dxf.start.x, l.dxf.start.y), ll(l.dxf.end.x, l.dxf.end.y)))
    for pl in msp.query("LWPOLYLINE"):
        pts = [(x, y) for x, y, *_ in pl.get_points()]
        if pl.closed and pts: pts.append(pts[0])
        out.append("<Placemark><LineString><coordinates>%s</coordinates></LineString></Placemark>" % " ".join(ll(x, y) for x, y in pts))
    if not out: abort(400, "لا توجد نقاط أو خطوط")
    kml = ('<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>%s</Document></kml>' % "".join(out)).encode()
    if request.form.get("fmt") == "kmz":
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z: z.writestr("doc.kml", kml)
        return send_file(io.BytesIO(b.getvalue()), as_attachment=True, download_name="survey.kmz")
    return send_file(io.BytesIO(kml), as_attachment=True, download_name="survey.kml")


@app.route("/transform_coords", methods=["POST"])
def transform_coords():
    try: n, e = float(request.form["north"]), float(request.form["east"])
    except (KeyError, ValueError): abort(400, "أدخل أرقامًا صحيحة")
    lon, lat = tf(request.form).transform(e, n)
    return ("<meta charset=utf-8><body dir=rtl style='font-family:sans-serif;padding:20px'><p>Lat: %.6f<br>Lon: %.6f</p>"
            "<a href='https://www.google.com/maps?q=%.6f,%.6f'>Google Maps</a> | <a href=/>رجوع</a>" % (lat, lon, lat, lon))


if __name__ == "__main__":
    app.run(port=5000)  # production: gunicorn app:app
