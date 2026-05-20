from flask import Flask, render_template, request, jsonify, send_file
import os
import io
import base64
import pickle
import rasterio
import rasterio.windows
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.colors as mcolors
import re
import uuid

from werkzeug.utils import secure_filename
from threading import Lock

app = Flask(__name__)

# ==========================================
# Upload Settings
# ==========================================

# Allow huge uploads (5GB)
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024 * 1024

# Force Werkzeug to stream uploads to disk
app.config['MAX_FORM_MEMORY_SIZE'] = 10 * 1024 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')

CHUNK_FOLDER = os.path.join(BASE_DIR, 'chunks')

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

os.makedirs(CHUNK_FOLDER, exist_ok=True)

upload_lock = Lock()

# ==========================================
# Helper Functions
# ==========================================

def parse_mtl_full(mtl_text):

    params = {
        'mult': {},
        'add': {},
        'sun_elev': 90.0,
        'sun_azimuth': 0.0,
        'scene_id': 'N/A',
        'date': 'N/A',
        'sensor': 'N/A',
        'path': 'N/A',
        'row': 'N/A',
        'cloud_cover': '0.0'
    }

    for line in mtl_text.split('\n'):

        line = line.strip()

        if '=' in line:

            key, val = line.split('=', 1)

            key = key.strip()

            val = val.strip().replace('"', '')

            if 'REFLECTANCE_MULT_BAND_' in key:

                band = int(
                    re.search(r'BAND_(\d+)', key).group(1)
                )

                params['mult'][band] = float(val)

            elif 'REFLECTANCE_ADD_BAND_' in key:

                band = int(
                    re.search(r'BAND_(\d+)', key).group(1)
                )

                params['add'][band] = float(val)

            elif key == 'SUN_ELEVATION':

                params['sun_elev'] = float(val)

            elif key == 'SUN_AZIMUTH':

                params['sun_azimuth'] = float(val)

            elif key in [
                'LANDSAT_PRODUCT_ID',
                'LANDSAT_SCENE_ID'
            ]:

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

    buf = io.BytesIO()

    fig.savefig(
        buf,
        format='png',
        bbox_inches='tight',
        pad_inches=0.1,
        facecolor='#0e1420'
    )

    buf.seek(0)

    img_b64 = base64.b64encode(
        buf.getvalue()
    ).decode('utf-8')

    plt.close(fig)

    return img_b64


def normalize_rgb(r, g, b):

    rgb = np.dstack((r, g, b))

    p2, p98 = np.percentile(rgb, (2, 98))

    rgb_s = np.clip(rgb, p2, p98)

    return (
        (rgb_s - p2) /
        (p98 - p2 + 1e-5)
    )

def get_band_crop(band_num, r0, r1, c0, c1):
    """Helper function to read a specific cropped window from a band."""
    path = os.path.join(UPLOAD_FOLDER, f'B{band_num}.tif')
    with rasterio.open(path) as src:
        window = rasterio.windows.Window(col_off=c0, row_off=r0, width=c1-c0, height=r1-r0)
        return src.read(1, window=window).astype(np.float32)


# ==========================================
# Routes
# ==========================================

@app.route('/')
def index():
    return send_file('index.html')

# ==========================================
# Chunk Upload Endpoint
# ==========================================
@app.route('/api/upload_chunk', methods=['POST'])
def upload_chunk():
    try:
        chunk = request.files.get('chunk')
        if not chunk:
            return jsonify({'success': False, 'error': 'Missing chunk'})

        filename = secure_filename(request.form['filename'])
        allowed = ['.tif', '.tiff', '.txt', '.pkl']
        if not any(filename.lower().endswith(ext) for ext in allowed):
            return jsonify({'success': False, 'error': 'Invalid file type'})

        chunk_index = int(request.form['chunkIndex'])
        file_id = request.form.get('fileId', str(uuid.uuid4()))
        
        file_folder = os.path.join(CHUNK_FOLDER, file_id)
        os.makedirs(file_folder, exist_ok=True)
        
        chunk_path = os.path.join(file_folder, f'chunk_{chunk_index}')

        # Save chunk
        if os.path.exists(chunk_path):
            return jsonify({'success': True, 'message': 'Chunk already uploaded'})
            
        chunk.save(chunk_path)

        return jsonify({
            'success': True,
            'completed': False,
            'message': f'Chunk {chunk_index} saved'
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==========================================
# Assemble File Endpoint
# ==========================================
@app.route('/api/assemble', methods=['POST'])
def assemble_file():
    try:
        data = request.get_json()
        filename = secure_filename(data['filename'])
        file_id = data['fileId']
        total_chunks = int(data['totalChunks'])

        file_folder = os.path.join(CHUNK_FOLDER, file_id)
        final_path = os.path.join(UPLOAD_FOLDER, filename)

        with upload_lock:
            with open(final_path, 'wb') as final_file:
                for i in range(total_chunks):
                    current_chunk = os.path.join(file_folder, f'chunk_{i}')
                    
                    if not os.path.exists(current_chunk):
                        return jsonify({'success': False, 'error': f'Missing chunk_{i}'}), 400

                    with open(current_chunk, 'rb') as chunk_file:
                        while True:
                            chunk_data = chunk_file.read(1024 * 1024)
                            if not chunk_data:
                                break
                            final_file.write(chunk_data)

        # Cleanup chunks
        for f in os.listdir(file_folder):
            os.remove(os.path.join(file_folder, f))
        os.rmdir(file_folder)

        return jsonify({
            'success': True,
            'completed': True,
            'filename': filename
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# ==========================================
# Finalize Upload
# ==========================================

@app.route('/api/upload', methods=['POST'])
def api_upload():

    try:

        # =====================================
        # Verify Required Files
        # =====================================
        for i in range(1, 8):

            path = os.path.join(
                UPLOAD_FOLDER,
                f'B{i}.tif'
            )

            if not os.path.exists(path):

                return jsonify({

                    'success': False,

                    'error': f'Band {i} not uploaded yet'
                })

        # =====================================
        # Verify MTL
        # =====================================
        mtl_path = os.path.join(
            UPLOAD_FOLDER,
            'MTL.txt'
        )

        if not os.path.exists(mtl_path):

            return jsonify({

                'success': False,

                'error': 'MTL.txt not uploaded yet'
            })

        # =====================================
        # Parse Metadata
        # =====================================
        with open(
            mtl_path,
            'r',
            encoding='utf-8'
        ) as f:

            mtl_params = parse_mtl_full(
                f.read()
            )

        # =====================================
        # Read Band 1 Metadata
        # =====================================
        with rasterio.open(
            os.path.join(
                UPLOAD_FOLDER,
                'B1.tif'
            )
        ) as src:

            full_height, full_width = src.shape

            crs = (
                src.crs.to_string()
                if src.crs
                else 'Unknown'
            )

            pixel_size = src.res[0]

        # =====================================
        # Build Response
        # =====================================
        meta_response = {

            'scene_id': mtl_params['scene_id'],

            'date': mtl_params['date'],

            'sensor': mtl_params['sensor'],

            'path': mtl_params['path'],

            'row': mtl_params['row'],

            'sun_elevation': mtl_params['sun_elev'],

            'sun_azimuth': mtl_params['sun_azimuth'],

            'cloud_cover': mtl_params['cloud_cover'],

            'crs': crs,

            'pixel_size_m': pixel_size,

            'full_height': full_height,

            'full_width': full_width
        }

        calibration_list = []

        for b in range(1, 8):

            calibration_list.append({

                'band': b,

                'M': mtl_params['mult'].get(
                    b,
                    2.0E-5
                ),

                'A': mtl_params['add'].get(
                    b,
                    -0.1
                )
            })

        return jsonify({

            'success': True,

            'message': 'All Landsat files uploaded successfully',

            'meta': meta_response,

            'calibration': calibration_list
        })

    except Exception as e:

        return jsonify({

            'success': False,

            'error': str(e)

        }), 500

# ==========================================
# Core Processing Endpoints
# ==========================================

@app.route('/api/preview', methods=['POST'])
def api_preview():
    try:
        data = request.get_json()
        r0, r1 = int(data['row_start']), int(data['row_end'])
        c0, c1 = int(data['col_start']), int(data['col_end'])

        b2 = get_band_crop(2, r0, r1, c0, c1)
        b3 = get_band_crop(3, r0, r1, c0, c1)
        b4 = get_band_crop(4, r0, r1, c0, c1)
        b5 = get_band_crop(5, r0, r1, c0, c1)

        # 1. True Color
        rgb = normalize_rgb(b4, b3, b2)
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.imshow(rgb)
        ax.axis('off')
        preview_rgb = fig_to_base64(fig)

        # 2. False Color
        false_color = normalize_rgb(b5, b4, b3)
        fig, ax = plt.subplots(figsize=(6, 6))
        ax.imshow(false_color)
        ax.axis('off')
        preview_false = fig_to_base64(fig)

        return jsonify({
            'success': True,
            'preview_rgb': preview_rgb,
            'preview_false': preview_false
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/classify', methods=['POST'])
def api_classify():
    try:
        r0 = int(request.form['row_start'])
        r1 = int(request.form['row_end'])
        c0 = int(request.form['col_start'])
        c1 = int(request.form['col_end'])
        height = r1 - r0
        width = c1 - c0

        # 1. قراءة وإعداد المودل
        model_file = request.files.get('model')
        if model_file:
            model = pickle.load(model_file)
        else:
            default_model_path = os.path.join(BASE_DIR, 'Outputs', 'best_model.pkl')
            if not os.path.exists(default_model_path):
                return jsonify({'success': False, 'error': 'No model uploaded and default model not found in Outputs/.'})
            with open(default_model_path, 'rb') as f:
                model = pickle.load(f)

        # 2. قراءة بيانات الـ MTL للحصول على معاملات الـ TOA Calibration
        mtl_path = os.path.join(UPLOAD_FOLDER, 'MTL.txt')
        with open(mtl_path, 'r', encoding='utf-8') as f:
            mtl_params = parse_mtl_full(f.read())
        sun_elev_rad = np.radians(mtl_params['sun_elev'])

        # 3. قراءة الـ 7 نطاقات ومعايرتها (TOA Reflectance)
        bands_toa = []
        for b in range(1, 8):
            raw_band = get_band_crop(b, r0, r1, c0, c1)
            M = mtl_params['mult'].get(b, 2.0E-5)
            A = mtl_params['add'].get(b, -0.1)
            toa = (M * raw_band + A) / np.sin(sun_elev_rad)
            bands_toa.append(toa)

        B1, B2, B3, B4, B5, B6, B7 = bands_toa

        # 4. حساب المؤشرات الطيفية وتأمين نوع البيانات (float32)
        np.seterr(divide='ignore', invalid='ignore')
        ndvi = np.where((B5 + B4) == 0., 0, (B5 - B4) / (B5 + B4)).astype(np.float32)
        mndwi = np.where((B3 + B6) == 0., 0, (B3 - B6) / (B3 + B6)).astype(np.float32)
        ndbi = np.where((B6 + B5) == 0., 0, (B6 - B5) / (B6 + B5)).astype(np.float32)

        # 5. تجهيز البيانات للمودل (Stacking)
        features = np.dstack((B1, B2, B3, B4, B5, B6, B7, ndvi, mndwi, ndbi))
        X = features.reshape(-1, 10)
        X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

        # 6. التنبؤ (Prediction)
        predictions = model.predict(X)

        # ========================================================
        # حل مشكلة الـ dtype object والـ String Labels ديناميكياً
        # ========================================================
        if not np.issubdtype(predictions.dtype, np.number):
            # الموديل يخرج نصوصاً (مثل كلمات 'Water', 'Vegetation'...)
            unique_labels = sorted(list(np.unique(predictions)))
            label_to_idx = {label: idx for idx, label in enumerate(unique_labels)}
            
            class_mapping = {}
            default_colors = ['#0055FF', '#228B22', '#E60000', '#FFD700', '#964B00', '#808080']
            
            for idx, label in enumerate(unique_labels):
                lbl_lower = str(label).lower()
                color = default_colors[idx % len(default_colors)]
                
                # تخمين اللون المناسب بناءً على اسم الفئة المكتوب في الموديل
                if 'wat' in lbl_lower: color = '#0055FF'
                elif 'veg' in lbl_lower or 'forest' in lbl_lower or 'crop' in lbl_lower: color = '#228B22'
                elif 'urb' in lbl_lower or 'built' in lbl_lower or 'build' in lbl_lower: color = '#E60000'
                elif 'bar' in lbl_lower or 'soil' in lbl_lower or 'des' in lbl_lower: color = '#FFD700'
                
                class_mapping[idx] = {'name': str(label), 'color': color}
                
            # تحويل النصوص إلى أرقام صريحة (int32) لحل مشكلة الماتبلوتليب
            numeric_predictions = np.array([label_to_idx[p] for p in predictions], dtype=np.int32)
            pred_map = numeric_predictions.reshape(height, width)
            unique, counts = np.unique(numeric_predictions, return_counts=True)
            pred_counts = dict(zip(unique, counts))
        else:
            # الموديل يخرج أرقاماً عادية [0, 1, 2, 3]
            predictions = predictions.astype(np.int32)
            pred_map = predictions.reshape(height, width)
            unique, counts = np.unique(predictions, return_counts=True)
            pred_counts = dict(zip(unique, counts))
            
            class_mapping = {
                0: {'name': 'Water', 'color': '#0055FF'},
                1: {'name': 'Vegetation', 'color': '#228B22'},
                2: {'name': 'Built-up / Urban', 'color': '#E60000'},
                3: {'name': 'Bare Soil / Desert', 'color': '#FFD700'}
            }
        
        cmap = mcolors.ListedColormap([c['color'] for c in class_mapping.values()])
        
        # 7. توليد صور الـ Base64 للخريطة والمؤشرات
        def create_image(data, cmap_name, is_index=False):
            fig, ax = plt.subplots(figsize=(6, 6))
            if is_index:
                ax.imshow(data, cmap=cmap_name, vmin=-1, vmax=1)
            else:
                ax.imshow(data, cmap=cmap_name)
            ax.axis('off')
            return fig_to_base64(fig)

        map_b64 = create_image(pred_map, cmap, is_index=False)
        ndvi_b64 = create_image(ndvi, 'RdYlGn', is_index=True)
        mndwi_b64 = create_image(mndwi, 'Blues', is_index=True)
        ndbi_b64 = create_image(ndbi, 'YlOrBr', is_index=True)

        # 8. حساب الإحصائيات والمساحات
        total_pixels = height * width
        pixel_area_km2 = (30 * 30) / 1_000_000 
        
        stats = []
        for cls_val, cls_info in class_mapping.items():
            count = pred_counts.get(cls_val, 0)
            area = count * pixel_area_km2
            pct = (count / total_pixels) * 100 if total_pixels > 0 else 0
            stats.append({
                'class': cls_info['name'],
                'color': cls_info['color'],
                'pixels': int(count),
                'area_km2': round(area, 2),
                'pct': round(pct, 1)
            })

        return jsonify({
            'success': True,
            'step4_map': map_b64,
            'step2_imgs': {
                'NDVI': ndvi_b64,
                'MNDWI': mndwi_b64,
                'NDBI': ndbi_b64
            },
            'subset': {
                'r0': r0, 'r1': r1, 'c0': c0, 'c1': c1,
                'h': height, 'w': width,
                'pixels': total_pixels,
                'area_km2': round(total_pixels * pixel_area_km2, 2)
            },
            'stats': stats
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
# ==========================================
# Download Stats Endpoint
# ==========================================
@app.route('/api/download_stats', methods=['POST'])
def download_stats():
    try:
        data = request.get_json()
        stats = data.get('stats', [])
        
        # Convert to DataFrame
        df = pd.DataFrame(stats)
        
        # Create a CSV in memory
        si = io.StringIO()
        df.to_csv(si, index=False)
        output = io.BytesIO()
        output.write(si.getvalue().encode('utf-8'))
        output.seek(0)
        
        return send_file(
            output,
            mimetype='text/csv',
            as_attachment=True,
            download_name='LULC_Stats.csv'
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)