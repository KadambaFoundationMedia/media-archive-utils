/* The waveform editor for the Kirtan splitter review (SPEC.md section 8).
 *
 * Alpine component `cutEditor`: a waveform (wavesurfer.js, regions plugin) with the kirtan and
 * the class as two coloured, labelled regions that meet at the cut. Drag the meeting edge, nudge
 * it, or type the time: all three change the same value, which the form submits as `split_at`.
 * The peaks and a browser-playable copy of the audio come from the server
 * (/files/<id>/waveform/peaks and /audio); the cut itself is always made on the real file.
 */
(function () {
  const AUDITION_S = 3;

  function formatTime(t) {
    // hh:mm:ss.mmm, the same as the server's format_time
    const ms = Math.round(t * 1000);
    const h = Math.floor(ms / 3600000);
    const m = Math.floor((ms % 3600000) / 60000);
    const s = (ms % 60000) / 1000;
    const two = (n) => (n < 10 ? "0" : "") + n;
    return two(h) + ":" + two(m) + ":" + (s < 10 ? "0" : "") + s.toFixed(3);
  }

  function parseTime(text) {
    // "28:13.048", "1:02:03" or "90.5" -> seconds; null when it is not a time
    const parts = String(text || "").trim().split(":");
    if (!parts[0] || parts.length > 3) return null;
    let seconds = 0;
    for (const part of parts) {
      const n = Number(part);
      if (!isFinite(n) || n < 0 || part.trim() === "") return null;
      seconds = seconds * 60 + n;
    }
    return seconds;
  }

  function rgba(hex, alpha) {
    const n = parseInt(hex.slice(1), 16);
    return "rgba(" + (n >> 16) + "," + ((n >> 8) & 255) + "," + (n & 255) + "," + alpha + ")";
  }

  function label(text, color) {
    const el = document.createElement("span");
    el.textContent = text;
    el.className = "cut-region-label";
    el.style.background = color;
    return el;
  }

  function decodePeaks(base64) {
    const bin = atob(base64);
    const out = new Float32Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i) / 255;
    return out;
  }

  document.addEventListener("alpine:init", () => {
    Alpine.data("cutEditor", (cfg) => ({
      cut: cfg.cut,
      cutText: cfg.cut == null ? "" : formatTime(cfg.cut),
      kirtanStart: cfg.kirtanStart || 0,
      duration: 0,
      status: "loading", // loading | ready | error
      error: "",
      playing: false,
      now: 0,
      zoomLevel: 4,
      mantra: cfg.mantra,
      review: !!cfg.review, // a Kirtan splitter review: the cut can be set; else the player only plays
      _ws: null,
      _regions: null,
      _kirtan: null,
      _class: null,
      _busy: false,
      _stopAt: null,

      formatTime,

      async init() {
        try {
          const response = await fetch("/files/" + cfg.fileId + "/waveform/peaks");
          if (!response.ok) throw new Error((await response.text()) || response.statusText);
          const data = await response.json();
          this.duration = data.duration;
          this._build(data);
        } catch (err) {
          this.status = "error";
          this.error = String(err.message || err).slice(0, 300);
        }
      },

      _build(data) {
        const style = getComputedStyle(document.documentElement);
        const dark = document.documentElement.getAttribute("data-bs-theme") === "dark";
        const regions = WaveSurfer.Regions.create();
        // the time under the mouse, so a place in the waveform can be read off before clicking it
        const plugins = [regions];
        if (WaveSurfer.Hover) {
          plugins.push(
            WaveSurfer.Hover.create({
              lineColor: "#0d6efd",
              lineWidth: 1,
              labelBackground: dark ? "#212529" : "#ffffff",
              labelColor: dark ? "#f8f9fa" : "#212529",
              labelSize: "12px",
              formatTimeCallback: formatTime,
            })
          );
        }
        const ws = WaveSurfer.create({
          container: this.$refs.wave,
          url: "/files/" + cfg.fileId + "/waveform/audio",
          peaks: [decodePeaks(data.peaks)],
          duration: data.duration,
          height: 150,
          normalize: true,
          minPxPerSec: this.zoomLevel,
          waveColor: dark ? "#8b949e" : "#6c757d",
          progressColor: dark ? "#c9d1d9" : "#343a40",
          cursorColor: "#dc3545",
          cursorWidth: 2,
          autoScroll: true,
          autoCenter: false,
          dragToSeek: false,
          plugins,
        });
        this._ws = ws;
        this._regions = regions;
        ws.on("timeupdate", (t) => {
          this.now = t;
          if (this._stopAt !== null && t >= this._stopAt) {
            this._stopAt = null;
            ws.pause();
          }
        });
        ws.on("play", () => (this.playing = true));
        ws.on("pause", () => (this.playing = false));
        ws.on("error", (err) => {
          this.status = "error";
          this.error = String(err && err.message ? err.message : err);
        });
        regions.on("region-update", (region) => this._moved(region));
        regions.on("region-updated", (region) => this._moved(region));
        ws.once("decode", () => {
          this.status = "ready";
          if (this.cut != null) this._drawRegions();
          this.zoomToCut();
        });
        // some browsers only fire 'ready' once the audio can play
        ws.once("ready", () => {
          if (this.status === "loading") {
            this.status = "ready";
            if (this.cut != null) this._drawRegions();
            this.zoomToCut();
          }
        });
      },

      _drawRegions() {
        if (this._kirtan) return;
        const colors = cfg.colors;
        const mantraName = () => this.mantra || "Kirtan";
        this._kirtan = this._regions.addRegion({
          id: "kirtan",
          start: this.kirtanStart,
          end: this.cut,
          color: rgba(colors.kirtan, 0.28),
          content: label(mantraName(), colors.kirtan),
          drag: false,
          resize: true,
        });
        this._class = this._regions.addRegion({
          id: "class",
          start: this.cut,
          end: this.duration,
          color: rgba(colors.class, 0.22),
          content: label("Class", colors.class),
          drag: false,
          resize: true,
        });
      },

      // The two regions always meet at the cut: only the shared edge may move.
      _moved(region) {
        if (this._busy || !this._kirtan || !this._class) return;
        let t = region.id === "kirtan" ? region.end : region.start;
        this.setCut(t, { fromRegion: true });
      },

      setCut(seconds, opts) {
        if (seconds == null || isNaN(seconds)) return;
        const lo = this.kirtanStart + 1;
        const hi = (this.duration || seconds) - 1;
        const t = Math.min(Math.max(seconds, lo), Math.max(lo, hi));
        this.cut = Math.round(t * 1000) / 1000;
        this.cutText = formatTime(this.cut);
        if (this._regions && !this._kirtan) this._drawRegions();
        if (this._kirtan && this._class) {
          this._busy = true;
          this._kirtan.setOptions({ start: this.kirtanStart, end: this.cut });
          this._class.setOptions({ start: this.cut, end: this.duration });
          this._busy = false;
        }
      },

      typed() {
        const t = parseTime(this.cutText);
        if (t === null) {
          this.cutText = this.cut == null ? "" : formatTime(this.cut);
          return;
        }
        this.setCut(t);
        this.audition();
      },

      nudge(delta) {
        if (this.cut == null) return;
        this.setCut(this.cut + delta);
        this.audition();
      },

      cutHere() {
        if (this._ws) this.setCut(this._ws.getCurrentTime());
      },

      // ±3 s around the cut: the kirtan's last seconds, then the class's first
      audition() {
        if (this.cut == null || !this._ws) return;
        this._playBetween(this.cut - AUDITION_S, this.cut + AUDITION_S);
      },

      beforeCut() {
        if (this.cut != null) this._playBetween(this.cut - AUDITION_S, this.cut);
      },

      fromCut() {
        if (this.cut != null) this._playBetween(this.cut, null);
      },

      _playBetween(from, to) {
        this._stopAt = to;
        this._ws.setTime(Math.max(0, from));
        this._ws.play();
      },

      toggle() {
        if (this._ws) {
          this._stopAt = null;
          this._ws.playPause();
        }
      },

      zoom() {
        if (this._ws) this._ws.zoom(Number(this.zoomLevel));
      },

      zoomToCut() {
        if (!this._ws) return;
        if (this.cut == null) return;
        this.zoomLevel = 40;
        this._ws.zoom(40);
        // the waveform is drawn again after a zoom: scroll once it has its new width
        setTimeout(() => this._ws && this._ws.setScrollTime(Math.max(0, this.cut - 15)), 150);
      },

      seekKey(event) {
        // space plays and pauses, unless the person is typing
        const tag = (event.target.tagName || "").toLowerCase();
        if (event.code === "Space" && !["input", "textarea", "select", "button"].includes(tag)) {
          event.preventDefault();
          this.toggle();
        }
      },

      mantraChanged(name) {
        this.mantra = name;
        const content = this._kirtan && this._kirtan.content;
        if (content) content.textContent = name;
      },
    }));
  });
})();
