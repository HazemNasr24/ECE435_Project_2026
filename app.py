from flask import Flask, render_template, request, jsonify, send_file
import os
import io
import base64
import pickle
import rasterio
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

if __name__ == '__main__':
    app.run(debug=True, port=5000)