from flask import Flask, request, jsonify, send_file
import qrcode
import re
import csv
import io
import os
import zipfile
import base64
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

VEOLIA_RED = "#ED1C24"
LOGO_B64   = None  # sera charge au demarrage

def load_font(size, bold=False):
    paths = [
        f"/usr/share/fonts/truetype/dejavu/DejaVuSans{'-Bold' if bold else ''}.ttf",
        f"/usr/share/fonts/truetype/liberation/LiberationSans{'-Bold' if bold else ''}.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            try: return ImageFont.truetype(p, size)
            except: pass
    return ImageFont.load_default()

def wrap_text(text, font, max_width, draw):
    words = text.split()
    lines, current = [], ""
    for word in words:
        test = (current + " " + word).strip()
        if draw.textbbox((0,0), test, font=font)[2] <= max_width:
            current = test
        else:
            if current: lines.append(current)
            current = word
    if current: lines.append(current)
    return lines

def generate_qr_image(ref, extra_fields, logo_img=None):
    qr = qrcode.QRCode(version=1, error_correction=qrcode.constants.ERROR_CORRECT_H, box_size=10, border=4)
    qr.add_data(ref)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color=VEOLIA_RED, back_color="white").convert("RGBA")
    qr_w, qr_h = qr_img.size

    if logo_img:
        logo_size = int(qr_w * 0.22)
        logo = logo_img.resize((logo_size, logo_size), Image.LANCZOS)
        bg_pad = 6
        bg_size = logo_size + bg_pad * 2
        bg = Image.new("RGBA", (bg_size, bg_size), (255,255,255,255))
        bx = (qr_w - bg_size) // 2
        by = (qr_h - bg_size) // 2
        qr_img.paste(bg,   (bx, by), bg)
        qr_img.paste(logo, (bx + bg_pad, by + bg_pad), logo)

    qr_img = qr_img.convert("RGB")

    font_ref   = load_font(22, bold=True)
    font_value = load_font(16)
    padding    = 12
    line_gap   = 4
    max_text_w = qr_w - 2 * padding

    dummy_draw = ImageDraw.Draw(Image.new("RGB", (1,1)))
    text_blocks = []
    text_blocks.append((wrap_text(f"Reference : {ref}", font_ref, max_text_w, dummy_draw), font_ref, "black"))
    for label, value in extra_fields:
        text_blocks.append((wrap_text(f"{label} : {value}", font_value, max_text_w, dummy_draw), font_value, "#333333"))

    total_text_h = padding
    for lines, font, _ in text_blocks:
        for line in lines:
            total_text_h += dummy_draw.textbbox((0,0), line, font=font)[3] + line_gap
    total_text_h += padding

    final = Image.new("RGB", (qr_w, total_text_h + qr_h), "white")
    draw  = ImageDraw.Draw(final)
    y = padding
    for lines, font, color in text_blocks:
        for line in lines:
            _, _, lw, lh = draw.textbbox((0,0), line, font=font)
            draw.text(((qr_w - lw) // 2, y), line, font=font, fill=color)
            y += lh + line_gap
    draw.line([(padding, total_text_h-1),(qr_w-padding, total_text_h-1)], fill="#cccccc", width=1)
    final.paste(qr_img, (0, total_text_h))
    return final

def get_prefix(ref):
    m = re.match(r'^([A-Za-z]+)', ref)
    if m: return m.group(1).upper()
    m = re.match(r'^(\d+)', ref)
    if m: return m.group(1)[:3]
    return 'AUTRES'

def sanitize(name):
    return re.sub(r'[\\/*?:"<>|]', '', name).strip()

def parse_csv(content):
    lines = content.splitlines()
    if not lines: return [], []
    first = lines[0].split(',')[0].strip().strip('"')
    has_header = not re.match(r'^[A-Z]{2,}[0-9]', first, re.IGNORECASE)
    reader  = csv.reader(lines)
    headers = []
    rows, seen = [], set()
    for i, row in enumerate(reader):
        if i == 0 and has_header:
            headers = [h.strip().strip('"') for h in row]
            continue
        if not row or not row[0].strip(): continue
        clean = [c.strip().strip('"') for c in row]
        ref = clean[0]
        if not ref or ref in seen: continue
        seen.add(ref)
        rows.append(clean)
    return rows, headers

@app.route('/')
def index():
    return open(os.path.join(os.path.dirname(__file__), 'index.html')).read()

@app.route('/parse', methods=['POST'])
def parse():
    f = request.files.get('csv')
    if not f: return jsonify({'error': 'Pas de fichier'}), 400
    content = f.read().decode('utf-8-sig')
    rows, headers = parse_csv(content)
    result = []
    for row in rows:
        ref = row[0]
        famille_idx = -1
        if headers:
            for i, h in enumerate(headers):
                if h.lower() in ('famille','family','categorie','category'):
                    famille_idx = i; break
        famille = row[famille_idx] if famille_idx != -1 and famille_idx < len(row) else get_prefix(ref)
        extra = []
        if headers:
            for i, h in enumerate(headers):
                if i == 0: continue
                if h.lower() in ('famille','family','categorie','category'): continue
                val = row[i] if i < len(row) else ''
                if val: extra.append([h.capitalize(), val])
        else:
            labels = ['Designation','Diametre','Col. 4','Col. 5']
            for i in range(1, len(row)):
                label = labels[i-1] if i-1 < len(labels) else f'Col. {i+1}'
                if row[i]: extra.append([label, row[i]])
        result.append({'ref': ref, 'famille': famille, 'extra': extra, 'row': row})
    return jsonify({'refs': result, 'headers': headers})

@app.route('/generate', methods=['POST'])
def generate():
    data    = request.json
    refs    = data.get('refs', [])
    logo_b64 = data.get('logo')

    logo_img = None
    if logo_b64:
        try:
            logo_data = base64.b64decode(logo_b64.split(',')[-1])
            logo_img  = Image.open(io.BytesIO(logo_data)).convert('RGBA')
        except: pass

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for item in refs:
            ref    = item['ref']
            famille = sanitize(item['famille']) or get_prefix(ref)
            extra  = [(e[0], e[1]) for e in item['extra']]
            img    = generate_qr_image(ref, extra, logo_img)
            img_buf = io.BytesIO()
            img.save(img_buf, format='PNG')
            zf.writestr(f"{famille}/{ref}.png", img_buf.getvalue())

    buf.seek(0)
    return send_file(buf, mimetype='application/zip', as_attachment=True, download_name='qrcodes.zip')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
