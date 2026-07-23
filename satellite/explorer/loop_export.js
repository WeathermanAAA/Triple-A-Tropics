/* loop_export.js — MP4 (H.264) loop encoder for the cockpit exports.
 *
 * WebCodecs VideoEncoder + the vendored Mp4Muxer (our CDN, fastStart
 * 'in-memory' so the moov atom leads and the file streams/saves like a
 * normal video on iOS/Safari — which has NO webm MediaRecorder at all).
 * Frame-by-frame: callers draw each frame onto a canvas and await
 * addFrame(), so encode quality never depends on wall-clock pacing.
 * When unavailable (no WebCodecs / muxer blocked / avc1 unsupported)
 * callers fall back to the legacy WebM path.
 */
(function () {
  'use strict';

  // H.264 level by pixel count (Main profile). Conservative: the level
  // must cover the frame size at ≤30 fps or configure() rejects.
  function levelHex(px) {
    if (px <= 921600) return '1f';     // 3.1 ≤ 1280x720
    if (px <= 2097152) return '28';    // 4.0 ≤ 1920x1080
    if (px <= 5652480) return '32';    // 5.0 ≤ ~2560x2048
    return '33';                       // 5.1
  }

  function pickConfig(w, h, fps, bitrate) {
    var lv = levelHex(w * h);
    var candidates = [
      'avc1.4d00' + lv,   // Main
      'avc1.42e0' + lv    // Constrained Baseline
    ];
    var base = { width: w, height: h, bitrate: bitrate,
                 framerate: fps, latencyMode: 'quality',
                 avc: { format: 'avc' } };
    function tryNext(k) {
      if (k >= candidates.length) return Promise.resolve(null);
      var cfg = Object.assign({ codec: candidates[k] }, base);
      return VideoEncoder.isConfigSupported(cfg).then(function (res) {
        return (res && res.supported) ? cfg : tryNext(k + 1);
      }, function () { return tryNext(k + 1); });
    }
    return tryNext(0);
  }

  var LoopExport = {
    // Cheap capability probe (no encoder is constructed).
    available: function () {
      return typeof VideoEncoder !== 'undefined'
        && typeof VideoFrame !== 'undefined'
        && typeof window.Mp4Muxer !== 'undefined';
    },

    /* create({width, height, fps, frames, maxBytes, maxBitrate})
     *   -> Promise<encoder|null>
     * encoder.addFrame(canvas) -> Promise (backpressure-aware)
     * encoder.finish() -> Promise<{blob, ext:'mp4'}>
     * Budget: same math as the WebM path, with an 8% safety margin so
     * the muxed file lands UNDER the byte budget, not around it.
     */
    create: function (opts) {
      if (!LoopExport.available()) return Promise.resolve(null);
      // H.264 4:2:0 needs even dimensions; frames are letterbox-free so
      // shaving one edge pixel is invisible.
      var w = opts.width & ~1, h = opts.height & ~1;
      if (w < 16 || h < 16) return Promise.resolve(null);
      var fps = opts.fps || 8;
      var secs = Math.max(1, (opts.frames || 48) / fps);
      var bitrate = Math.max(3e5, Math.min(
        opts.maxBitrate || 6e6,
        Math.floor((opts.maxBytes || 9e6) * 8 * 0.92 / secs)));

      return pickConfig(w, h, fps, bitrate).then(function (cfg) {
        if (!cfg) return null;
        var target = new Mp4Muxer.ArrayBufferTarget();
        var muxer = new Mp4Muxer.Muxer({
          target: target,
          video: { codec: 'avc', width: w, height: h },
          fastStart: 'in-memory'
        });
        var encErr = null;
        var encoder = new VideoEncoder({
          output: function (chunk, meta) {
            try { muxer.addVideoChunk(chunk, meta); }
            catch (e) { encErr = encErr || e; }
          },
          error: function (e) { encErr = encErr || e; }
        });
        encoder.configure(cfg);

        var n = 0, evenCanvas = null;
        var frameUs = Math.round(1e6 / fps);

        function drainQueue() {
          if (encoder.encodeQueueSize <= 8) return Promise.resolve();
          return new Promise(function (res) { setTimeout(res, 20); })
            .then(drainQueue);
        }

        return {
          addFrame: function (canvas) {
            if (encErr) return Promise.reject(encErr);
            var src = canvas;
            if (canvas.width !== w || canvas.height !== h) {
              if (!evenCanvas) {
                evenCanvas = document.createElement('canvas');
                evenCanvas.width = w; evenCanvas.height = h;
              }
              evenCanvas.getContext('2d').drawImage(canvas, 0, 0);
              src = evenCanvas;
            }
            return drainQueue().then(function () {
              if (encErr) throw encErr;
              var vf = new VideoFrame(src, {
                timestamp: n * frameUs, duration: frameUs
              });
              try {
                encoder.encode(vf, { keyFrame: n % (fps * 2) === 0 });
              } finally { vf.close(); }
              n++;
            });
          },
          finish: function () {
            return encoder.flush().then(function () {
              if (encErr) throw encErr;
              muxer.finalize();
              try { encoder.close(); } catch (e) {}
              return { blob: new Blob([target.buffer],
                                      { type: 'video/mp4' }),
                       ext: 'mp4' };
            });
          },
          abort: function () {
            try { encoder.close(); } catch (e) {}
          }
        };
      }, function () { return null; });
    }
  };

  window.LoopExport = LoopExport;
})();
