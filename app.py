from flask import Flask, render_template, request, jsonify, send_file
import os
import io
import base64
import pickle
import rasterio
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg') # لمنع أي مشاكل تتعلق بالواجهات الرسومية على السيرفر
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.colors as mcolors
import re

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ==========================================
# 1. Helper Functions
# ==========================================
def parse_mtl_full(mtl_text):
    """استخراج كافة بيانات الـ Metadata والمعايرة من ملف الـ MTL"""
    params = {
        'mult': {}, 'add': {}, 'sun_elev': 90.0, 'sun_azimuth': 0.0,
        'scene_id': 'N/A', 'date': 'N/A', 'sensor': 'N/A', 'path': 'N/A', 'row': 'N/A', 'cloud_cover': '0.0'
    }
    for line in mtl_text.split('\n'):
        line = line.strip()
        if '=' in line:
            key, val = line.split('=', 1)
            key = key.strip()
            val = val.strip().replace('"', '')
            
            if 'REFLECTANCE_MULT_BAND_' in key:
                band = int(re.search(r'BAND_(\d+)', key).group(1))
                params['mult'][band] = float(val)
            elif 'REFLECTANCE_ADD_BAND_' in key:
                band = int(re.search(r'BAND_(\d+)', key).group(1))
                params['add'][band] = float(val)
            elif key == 'SUN_ELEVATION':
                params['sun_elev'] = float(val)
            elif key == 'SUN_AZIMUTH':
                params['sun_azimuth'] = float(val)
            elif key == 'LANDSAT_PRODUCT_ID' or key == 'LANDSAT_SCENE_ID':
                params['scene_id'] = val
            elif key == 'DATE_ACQUIRED':
                params['date'] = val
            elif key == 'SPACECRAFT_ID':
                params['sensor'] = val
            elif key == 'WRS_PATH':
                params['path'] = val
            elif key == 'WRS_ROW':
                params['row'] = val
            elif key == 'CLOUD_COVER':
                params['cloud_cover'] = val
    return params

def fig_to_base64(fig):
    """تحويل رسمة Matplotlib إلى سلسلة Base64 ليعرضها المتصفح فوراً"""
    buf = io.BytesIO()
    fig.savefig(buf, format='png', bbox_inches='tight', pad_inches=0.1, facecolor='#0e1420')
    buf.seek(0)
    img_b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    plt.close(fig)
    return img_b64

def normalize_rgb(r, g, b):
    """تحسين تباين الألوان وعمل Stretch لعرض الصور بوضوح عالي"""
    rgb = np.dstack((r, g, b))
    p2, p98 = np.percentile(rgb, (2, 98))
    rgb_s = np.clip(rgb, p2, p98)
    return ((rgb_s - p2) / (p98 - p2 + 1e-5))

# ==========================================
# 2. Flask Routes (Endpoints)
# ==========================================

@app.route('/')
def index():
    return send_file('index.html')

@app.route('/api/upload', methods=['POST'])
def api_upload():
    """الخطوة 1: استقبال الملفات وقراءة معلومات الـ Scene والـ Calibration"""
    try:
        for i in range(1, 8):
            file = request.files.get(f'band{i}')
            if not file:
                return jsonify({'success': False, 'error': f'Missing Band {i}'})
            file.save(os.path.join(UPLOAD_FOLDER, f'B{i}.tif'))
            
        mtl_file = request.files.get('mtl')
        if not mtl_file:
            return jsonify({'success': False, 'error': 'Missing MTL.txt'})
        
        mtl_path = os.path.join(UPLOAD_FOLDER, 'MTL.txt')
        mtl_file.save(mtl_path)
        
        with open(mtl_path, 'r', encoding='utf-8') as f:
            mtl_params = parse_mtl_full(f.read())
            
        with rasterio.open(os.path.join(UPLOAD_FOLDER, 'B1.tif')) as src:
            full_height, full_width = src.shape
            crs = src.crs.to_string() if src.crs else 'Unknown'
            pixel_size = src.res[0]

        meta_response = {
            'scene_id': mtl_params['scene_id'], 'date': mtl_params['date'],
            'sensor': mtl_params['sensor'], 'path': mtl_params['path'], 'row': mtl_params['row'],
            'sun_elevation': mtl_params['sun_elev'], 'sun_azimuth': mtl_params['sun_azimuth'],
            'cloud_cover': mtl_params['cloud_cover'], 'crs': crs, 'pixel_size_m': pixel_size,
            'full_height': full_height, 'full_width': full_width
        }
        
        calibration_list = []
        for b in range(1, 8):
            calibration_list.append({
                'band': b, 'M': mtl_params['mult'].get(b, 2.0E-5), 'A': mtl_params['add'].get(b, -0.1)
            })
            
        return jsonify({'success': True, 'meta': meta_response, 'calibration': calibration_list})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/preview', methods=['POST'])
def api_preview():
    """الخطوة 2: قص منطقة المعاينة السريعة"""
    try:
        data = request.json
        r0, r1 = data['row_start'], data['row_end']
        c0, c1 = data['col_start'], data['col_end']
        window = rasterio.windows.Window.from_slices((r0, r1), (c0, c1))
        
        bands_data = {}
        for b in [2, 3, 4, 5]:
            with rasterio.open(os.path.join(UPLOAD_FOLDER, f'B{b}.tif')) as src:
                bands_data[b] = src.read(1, window=window).astype(np.float32)
                
        # True Color (4,3,2)
        rgb_raw = normalize_rgb(bands_data[4], bands_data[3], bands_data[2])
        fig_rgb, ax = plt.subplots(figsize=(5, 5))
        ax.imshow(rgb_raw)
        ax.axis('off')
        preview_rgb = fig_to_base64(fig_rgb)
        
        # False Color (5,4,3)
        false_raw = normalize_rgb(bands_data[5], bands_data[4], bands_data[3])
        fig_false, ax = plt.subplots(figsize=(5, 5))
        ax.imshow(false_raw)
        ax.axis('off')
        preview_false = fig_to_base64(fig_false)
        
        return jsonify({'success': True, 'preview_rgb': preview_rgb, 'preview_false': preview_false})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/classify', methods=['POST'])
def api_classify():
    """الخطوة 3 و 4: تشغيل خط المعالجة بالكامل واستخراج النتائج التدريجية"""
    try:
        r0 = int(request.form['row_start'])
        r1 = int(request.form['row_end'])
        c0 = int(request.form['col_start'])
        c1 = int(request.form['col_end'])
        window = rasterio.windows.Window.from_slices((r0, r1), (c0, c1))
        
        model_file = request.files.get('model')
        if model_file:
            model = pickle.load(model_file)
        else:
            # بما إن الموديل جوه Web في نفس المسار:
            model_path = os.path.join(os.getcwd(), 'best_model.pkl')
            with open(model_path, 'rb') as f:
                model = pickle.load(f)
                
        # --- خطوة 1: المعايرة الإشعاعية الفعلية وقص النطاق ---
        with open(os.path.join(UPLOAD_FOLDER, 'MTL.txt'), 'r', encoding='utf-8') as f:
            mtl_params = parse_mtl_full(f.read())
        sun_elev_rad = np.radians(mtl_params['sun_elev'])
        
        calibrated_bands = []
        nodata_mask = None
        
        for b in range(1, 8):
            with rasterio.open(os.path.join(UPLOAD_FOLDER, f'B{b}.tif')) as src:
                img_array = src.read(1, window=window).astype(np.float32)
                if nodata_mask is None:
                    nodata_mask = (img_array == 0)
                else:
                    nodata_mask = nodata_mask | (img_array == 0)
                    
                mult = mtl_params['mult'].get(b, 2.0E-5)
                add = mtl_params['add'].get(b, -0.1)
                calibrated = ((img_array * mult) + add) / np.sin(sun_elev_rad)
                calibrated_bands.append(calibrated)
                
        # رسم صورة المعايرة للمرحلة الأولى (True Color حقيقي تمت معايرته)
        rgb_cal = normalize_rgb(calibrated_bands[3], calibrated_bands[2], calibrated_bands[1]) # B4, B3, B2
        fig_step1, ax = plt.subplots(figsize=(6, 5))
        ax.imshow(rgb_cal)
        ax.axis('off')
        step1_img_b64 = fig_to_base64(fig_step1)
        
        # --- خطوة 2: استخراج المؤشرات الطيفية الـ 3 (NDVI, MNDWI, NDBI) ---
        green, red, nir, swir1 = calibrated_bands[2], calibrated_bands[3], calibrated_bands[4], calibrated_bands[5]
        np.seterr(divide='ignore', invalid='ignore')
        
        ndvi  = np.where((nir + red) == 0, 0, (nir - red) / (nir + red))
        mndwi = np.where((green + swir1) == 0, 0, (green - swir1) / (green + swir1)) # ✨ حساب MNDWI بدقة
        ndbi  = np.where((swir1 + nir) == 0, 0, (swir1 - nir) / (swir1 + nir))
        
        ndvi[nodata_mask], mndwi[nodata_mask], ndbi[nodata_mask] = 0, 0, 0
        
        indices_images = {}
        for name, arr, cmap in [('NDVI', ndvi, 'RdYlGn'), ('MNDWI', mndwi, 'Blues'), ('NDBI', ndbi, 'YlOrRd')]:
            fig_idx, ax = plt.subplots(figsize=(4, 4))
            ax.imshow(arr, cmap=cmap)
            ax.axis('off')
            indices_images[name] = fig_to_base64(fig_idx)
            
        # --- خطوة 3: الـ Layer Stacking ---
        stacked_bands = np.array(calibrated_bands)
        final_stack = np.vstack((stacked_bands, ndvi[np.newaxis, ...], mndwi[np.newaxis, ...], ndbi[np.newaxis, ...]))
        final_stack = np.nan_to_num(final_stack, nan=0.0, posinf=0.0, neginf=0.0)
        n_bands, n_rows, n_cols = final_stack.shape
        stack_msg = f"Stacked 7 Calibrated Bands + 3 Spectral Indices into a final {n_bands}-layer data matrix."
        
        # --- خطوة 4: التصنيف وعزل الـ NoData ---
        img_2d = final_stack.reshape(n_bands, -1).T
        predicted = model.predict(img_2d)
        classification_map = predicted.reshape(n_rows, n_cols)
        classification_map[nodata_mask] = 'NoData'
        
        class_colors = {'Water': '#0055FF', 'Desert': '#FFD700', 'Urban': '#E60000', 'Vegetation': '#228B22', 'NoData': '#FFFFFF'}
        ordered_classes = list(model.classes_)
        if 'NoData' not in ordered_classes and np.any(nodata_mask):
            ordered_classes.append('NoData')
            
        class_to_int = {cls: i for i, cls in enumerate(ordered_classes)}
        numeric_map = np.vectorize(class_to_int.get)(classification_map)
        colors_list = [class_colors.get(c, '#FFFFFF') for c in ordered_classes]
        
        fig_map, ax = plt.subplots(figsize=(8, 6))
        ax.imshow(numeric_map, cmap=mcolors.ListedColormap(colors_list))
        patches = [mpatches.Patch(color=class_colors[c], label=c) for c in ordered_classes if c != 'NoData']
        ax.legend(handles=patches, loc='lower right')
        ax.axis('off')
        step4_map_b64 = fig_to_base64(fig_map)
        
        # حساب الإحصائيات
        stats_list = []
        for cls in ordered_classes:
            count = int(np.sum(classification_map == cls))
            area_km2 = round((count * 900) / 1e6, 2)
            pct = round((count / (n_rows * n_cols)) * 100, 2)
            stats_list.append({
                'class': cls, 'pixels': count, 'area_km2': area_km2, 'pct': pct, 'color': class_colors.get(cls, '#fff')
            })
            
        return jsonify({
            'success': True,
            'step1_img': step1_img_b64,
            'step2_imgs': indices_images,
            'step3_info': stack_msg,
            'step4_map': step4_map_b64,
            'stats': stats_list,
            'subset': {'r0': r0, 'r1': r1, 'c0': c0, 'c1': c1, 'h': n_rows, 'w': n_cols, 'pixels': n_rows * n_cols, 'area_km2': round((n_rows * n_cols * 900) / 1e6, 1)}
        })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/download_stats', methods=['POST'])
def api_download_stats():
    try:
        data = request.json['stats']
        df = pd.DataFrame(data).drop(columns=['color'], errors='ignore')
        buf = io.BytesIO()
        df.to_csv(buf, index=False, encoding='utf-8')
        buf.seek(0)
        return send_file(buf, mimetype="text/csv", as_attachment=True, download_name="LULC_Stats.csv")
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    app.run(debug=True, port=5000)