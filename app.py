@app.route('/api/upload', methods=['POST'])
def api_upload():
    """Upload Landsat bands + MTL safely for huge files"""
    try:

        saved_files = []

        # ==============================
        # Save TIFF bands directly to disk
        # ==============================
        for i in range(1, 8):

            uploaded_file = request.files.get(f'band{i}')

            if not uploaded_file:
                return jsonify({
                    'success': False,
                    'error': f'Missing Band {i}'
                })

            filename = secure_filename(f'B{i}.tif')
            save_path = os.path.join(UPLOAD_FOLDER, filename)

            # remove old file if exists
            if os.path.exists(save_path):
                os.remove(save_path)

            # SAVE DIRECTLY TO DISK
            uploaded_file.save(save_path)

            saved_files.append(save_path)

        # ==============================
        # Save MTL
        # ==============================
        mtl_file = request.files.get('mtl')

        if not mtl_file:
            return jsonify({
                'success': False,
                'error': 'Missing MTL.txt'
            })

        mtl_path = os.path.join(UPLOAD_FOLDER, 'MTL.txt')

        if os.path.exists(mtl_path):
            os.remove(mtl_path)

        mtl_file.save(mtl_path)

        # ==============================
        # Parse MTL
        # ==============================
        with open(mtl_path, 'r', encoding='utf-8') as f:
            mtl_params = parse_mtl_full(f.read())

        # ==============================
        # Read metadata from Band 1
        # ==============================
        with rasterio.open(os.path.join(UPLOAD_FOLDER, 'B1.tif')) as src:

            full_height, full_width = src.shape

            crs = src.crs.to_string() if src.crs else 'Unknown'

            pixel_size = src.res[0]

        # ==============================
        # Response
        # ==============================
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
                'M': mtl_params['mult'].get(b, 2.0E-5),
                'A': mtl_params['add'].get(b, -0.1)
            })

        return jsonify({
            'success': True,
            'meta': meta_response,
            'calibration': calibration_list
        })

    except Exception as e:

        return jsonify({
            'success': False,
            'error': str(e)
        }), 500