// carr_stock_clip.jsx — CARR stock b-roll clip builder
// Reads Scripts/stockclip-job.json: a shot list (footage, durations, text beats)
// plus an end card. Builds a graded, animated comp and renders lossless;
// ffmpeg does the H.264 finish + audio mix afterward (make-stock-clip.sh).
//
// Per shot: cover-scaled footage, slow push-in, navy Tint grade, contrast,
// bottom legibility gradient, vignette, Oswald text with rise+fade, orange accent.
// Cuts are 10-frame cross dissolves. End card: navy, white CARR logo, name/title.

(function () {
    try {
        app.preferences.savePrefAsBool("Main Pref Section",
            "Pref_SCRIPTING_FILE_NETWORK_SECURITY", true, PREFType.PREF_Type_MACHINE_INDEPENDENT);
    } catch (e) { }

    var PIPE;
    try { PIPE = Folder(new File($.fileName).parent.parent).fsName; } catch (e) { }
    if (!PIPE || !Folder(PIPE + "/Scripts").exists) {
        PIPE = Folder("~/Movies/CARR Video Pipeline").fsName;
    }
    var LOG = new File(PIPE + "/Scripts/stockclip-log.txt");
    function log(msg) { LOG.open("a"); LOG.writeln(new Date().toTimeString().substr(0, 8) + "  " + msg); LOG.close(); }
    function fail(msg) { log("FAIL: " + msg); throw new Error(msg); }

    var jobFile = new File(PIPE + "/Scripts/stockclip-job.json");
    if (!jobFile.exists) fail("job file missing");
    jobFile.open("r"); var job = eval("(" + jobFile.read() + ")"); jobFile.close();

    try {
    var W = job.width, H = job.height, FPS = job.fps || 29.97;
    var NAVY = [0 / 255, 47 / 255, 108 / 255];
    var NAVY_DEEP = [3 / 255, 20 / 255, 45 / 255];
    var ORANGE = [245 / 255, 127 / 255, 41 / 255];
    var XFADE = 10 / FPS; // cross dissolve length

    // total duration
    var total = 0;
    for (var i = 0; i < job.shots.length; i++) total += job.shots[i].dur;
    if (job.endCard) total += job.endCard.dur;

    log("start: " + job.shots.length + " shots, " + W + "x" + H + ", " + total.toFixed(1) + "s");

    app.newProject();
    var comp = app.project.items.addComp(job.compName || "CARR_StockClip", W, H, 1, total, FPS);

    function easeKeys(prop, k1, k2) {
        var e = [new KeyframeEase(0, 66)];
        try { prop.setTemporalEaseAtKey(k1, e, e); prop.setTemporalEaseAtKey(k2, e, e); } catch (err) { }
    }

    var FONT_HEAD = ["Oswald-SemiBold", "Oswald-Bold", "Oswald", "Montserrat-Bold", "HelveticaNeue-Bold"];
    var FONT_BODY = ["Montserrat-SemiBold", "Montserrat-Bold", "HelveticaNeue-Medium", "HelveticaNeue"];
    var resolvedFont = null;

    // AE 2026 renders fallback glyphs when fonts are set by the legacy string
    // property; the font OBJECT API resolves reliably. Cache the font list once.
    var FONT_CACHE = {};
    (function () {
        try {
            var all = app.fonts.allFonts;
            for (var i = 0; i < all.length; i++)
                for (var j = 0; j < all[i].length; j++)
                    FONT_CACHE[all[i][j].postScriptName] = all[i][j];
        } catch (e) { log("font cache unavailable: " + e.toString()); }
    })();

    function setFont(tl, chain) {
        var td = tl.property("Source Text").value;
        for (var i = 0; i < chain.length; i++) {
            if (FONT_CACHE[chain[i]]) {
                try {
                    td.fontObject = FONT_CACHE[chain[i]];
                    tl.property("Source Text").setValue(td);
                    if (!resolvedFont) { resolvedFont = chain[i]; log("headline font (fontObject): " + chain[i]); }
                    return;
                } catch (e) { }
            }
        }
        for (var k = 0; k < chain.length; k++) { // legacy fallback
            td.font = chain[k];
            tl.property("Source Text").setValue(td);
            if (tl.property("Source Text").value.font === chain[k]) {
                if (!resolvedFont) { resolvedFont = chain[k]; log("headline font (legacy): " + chain[k]); }
                return;
            }
        }
    }

    function addGrade(layer) {
        var fx = layer.property("ADBE Effect Parade");
        var tint = fx.addProperty("ADBE Tint");
        tint.property("ADBE Tint-0001").setValue(NAVY_DEEP);       // map black
        tint.property("ADBE Tint-0002").setValue([0.97, 0.97, 1]); // map white
        tint.property("ADBE Tint-0003").setValue(32);              // amount
        var bc = fx.addProperty("ADBE Brightness & Contrast 2");
        bc.property("ADBE Brightness & Contrast 2-0001").setValue(-6);  // brightness
        bc.property("ADBE Brightness & Contrast 2-0002").setValue(14);  // contrast
    }

    // ---- shots ----
    var t = 0;
    var overlays = []; // text + accent layers to re-raise above grade layers at the end

    // measured text fitting (v4 critique: never let text leave the safe zone)
    function fitText(tl, maxW) {
        var td2 = tl.property("Source Text").value;
        for (var guard = 0; guard < 24; guard++) {
            var r = tl.sourceRectAtTime(tl.inPoint + 0.05, false);
            if (r.width <= maxW || td2.fontSize <= 18) break;
            td2.fontSize = Math.round(td2.fontSize * 0.94);
            tl.property("Source Text").setValue(td2);
        }
    }

    // ---- stat beat (escalator-style math cards) ----
    function statText(str, chain, size, color, x, y, tIn, tOut, just) {
        var tl = comp.layers.addText(str);
        setFont(tl, chain);
        var td3 = tl.property("Source Text").value;
        td3.applyFill = true;
        td3.fontSize = size; td3.fillColor = color;
        td3.justification = just || ParagraphJustification.CENTER_JUSTIFY;
        td3.tracking = 30;
        tl.property("Source Text").setValue(td3);
        tl.property("Position").setValue([x, y]);
        tl.inPoint = tIn; tl.outPoint = tOut;
        overlays.push(tl);
        return tl;
    }
    function popIn(tl, at) { // quick rise + fade, escalator-row style
        var op = tl.property("Opacity");
        op.setValueAtTime(at, 0);
        op.setValueAtTime(at + 0.28, 100);
        var p = tl.property("Position");
        var v = p.value;
        p.setValueAtTime(at, [v[0], v[1] + 16]);
        p.setValueAtTime(at + 0.32, v);
        easeKeys(p, 1, 2);
    }
    function statBeat(shot, t0) {
        var tEnd = t0 + shot.dur;
        var bg = comp.layers.addSolid(NAVY, "stat bg", W, H, 1);
        bg.inPoint = t0; bg.outPoint = tEnd;
        var bop = bg.property("Opacity");
        bop.setValueAtTime(t0, 0); bop.setValueAtTime(t0 + XFADE, 100);
        bop.setValueAtTime(tEnd - XFADE, 100); bop.setValueAtTime(tEnd, 0);

        var ORANGE_ = ORANGE, WHITE_ = [1, 1, 1], DIM = [0.72, 0.78, 0.88];
        var title = statText(shot.title, FONT_BODY, Math.round(W * 0.034), DIM,
            W / 2, H * 0.30, t0, tEnd);
        fitText(title, W * 0.9); popIn(title, t0 + 0.3);

        if (shot.kind === "counter") {
            var c = shot.counter;
            var big = statText(c.prefix + "0" + c.suffix, FONT_HEAD, Math.round(W * 0.14),
                c.orange ? ORANGE_ : WHITE_, W / 2, H * 0.50, t0, tEnd);
            var st = t0 + (c.delay || 0.7), en = st + (c.dur || 1.6);
            big.property("Source Text").expression =
                'var v = ease(time, ' + st + ', ' + en + ', 0, ' + c.target + ');\n' +
                '"' + c.prefix + '" + Math.round(v) + "' + c.suffix + '"';
            fitText(big, W * 0.9); popIn(big, t0 + 0.4);
            if (shot.sub) {
                var sub = statText(shot.sub, FONT_BODY, Math.round(W * 0.026), DIM,
                    W / 2, H * 0.62, t0, tEnd);
                fitText(sub, W * 0.88); popIn(sub, en + 0.15);
            }
        } else { // stack
            var y = H * 0.40, step = H * 0.085;
            for (var r = 0; r < shot.rows.length; r++) {
                var row = shot.rows[r], at = t0 + row.delay;
                var lab = statText(row.label, FONT_BODY, Math.round(W * 0.028), DIM,
                    W * 0.10, y, t0, tEnd, ParagraphJustification.LEFT_JUSTIFY);
                var val = statText(row.value, FONT_HEAD, Math.round(W * 0.042),
                    row.orange ? ORANGE_ : WHITE_,
                    W * 0.90, y, t0, tEnd, ParagraphJustification.RIGHT_JUSTIFY);
                fitText(lab, W * 0.52);
                popIn(lab, at); popIn(val, at + 0.12);
                y += step;
            }
            // divider + net counter
            var net = shot.net, nAt = t0 + net.delay;
            var divW = Math.round(W * 0.8), divH = Math.max(4, Math.round(H * 0.004));
            var div = comp.layers.addSolid(ORANGE_, "stat divider", divW, divH, 1);
            div.property("Anchor Point").setValue([0, divH / 2]);
            div.property("Position").setValue([W * 0.10, y + step * 0.15]);
            div.inPoint = t0; div.outPoint = tEnd;
            var dsc = div.property("Scale");
            dsc.setValueAtTime(nAt - 0.35, [0, 100]);
            dsc.setValueAtTime(nAt, [100, 100]);
            easeKeys(dsc, 1, 2);
            overlays.push(div);
            var ny = y + step * 0.85;
            var nlab = statText(net.label, FONT_BODY, Math.round(W * 0.030), DIM,
                W * 0.10, ny, t0, tEnd, ParagraphJustification.LEFT_JUSTIFY);
            // force style through a second pass — this one layer drops its first styling
            var ntd = nlab.property("Source Text").value;
            ntd.applyFill = true; ntd.fillColor = DIM;
            nlab.property("Source Text").setValue(ntd);
            popIn(nlab, nAt);
            var nval = statText(net.prefix + "0" + net.suffix, FONT_HEAD, Math.round(W * 0.058),
                ORANGE_, W * 0.90, ny, t0, tEnd, ParagraphJustification.RIGHT_JUSTIFY);
            var nEn = nAt + 1.3;
            nval.property("Source Text").expression =
                'var v = ease(time, ' + nAt + ', ' + nEn + ', 0, ' + net.target + ');\n' +
                '"' + net.prefix + '" + Math.round(v) + "' + net.suffix + '"';
            popIn(nval, nAt);
        }
    }
    for (var s = 0; s < job.shots.length; s++) {
        var shot = job.shots[s];
        if (shot.type === "stat") { statBeat(shot, t); t += shot.dur; continue; }
        var f = new File(shot.src);
        if (!f.exists) fail("footage missing: " + shot.src);
        var footage = app.project.importFile(new ImportOptions(f));
        var lyr = comp.layers.add(footage);
        lyr.startTime = t;
        lyr.inPoint = t;
        lyr.outPoint = Math.min(t + shot.dur + XFADE, total);

        // cover-fill + settle-in + slow push-in
        var cover = Math.max(W / footage.width, H / footage.height) * 100;
        var push = shot.pushIn || 1.07;
        var sc = lyr.property("Scale");
        if (t > 0) { // incoming shots land slightly wide and settle
            sc.setValueAtTime(lyr.inPoint, [cover * 0.985, cover * 0.985]);
            sc.setValueAtTime(lyr.inPoint + 0.5, [cover, cover]);
            easeKeys(sc, 1, 2);
        } else {
            sc.setValueAtTime(lyr.inPoint, [cover, cover]);
        }
        sc.setValueAtTime(lyr.outPoint, [cover * push, cover * push]);
        lyr.property("Position").setValue([W / 2, H / 2]);
        addGrade(lyr);

        // blur-through dissolve: outgoing softens + fades, incoming arrives soft and snaps in
        var blur = lyr.property("ADBE Effect Parade").addProperty("ADBE Gaussian Blur 2");
        var bl = blur.property("ADBE Gaussian Blur 2-0001");
        if (t > 0) {
            bl.setValueAtTime(lyr.inPoint, 16);
            bl.setValueAtTime(lyr.inPoint + XFADE, 0);
        }
        if (s < job.shots.length - 1 || job.endCard) {
            bl.setValueAtTime(lyr.outPoint - XFADE, 0);
            bl.setValueAtTime(lyr.outPoint, 16);
            var op = lyr.property("Opacity");
            op.setValueAtTime(lyr.outPoint - XFADE, 100);
            op.setValueAtTime(lyr.outPoint, 0);
        }

        // per-shot text beat — split long lines in two, cap size to fit width
        if (shot.line) {
            var lineText = shot.line;
            var lineCount = 1;
            if (lineText.length > 22) {
                var mid = Math.floor(lineText.length / 2);
                var best = -1;
                for (var c = 0; c < lineText.length; c++) {
                    if (lineText.charAt(c) === " " &&
                        (best < 0 || Math.abs(c - mid) < Math.abs(best - mid))) best = c;
                }
                if (best > 0) {
                    lineText = lineText.substr(0, best) + "\r" + lineText.substr(best + 1);
                    lineCount = 2;
                }
            }
            var segs = lineText.split("\r");
            var maxLen = 0;
            for (var g = 0; g < segs.length; g++) if (segs[g].length > maxLen) maxLen = segs[g].length;

            var tl = comp.layers.addText(lineText);
            setFont(tl, FONT_HEAD);
            var td = tl.property("Source Text").value;
            var fsize = Math.round(W * 0.062);
            td.fontSize = fsize;
            td.fillColor = [1, 1, 1];
            td.justification = ParagraphJustification.CENTER_JUSTIFY;
            td.tracking = 40;
            tl.property("Source Text").setValue(td);
            var ty = (lineCount > 1) ? H * 0.685 : H * 0.72;
            var tin = t + 0.45, tout = t + shot.dur;
            tl.inPoint = tin; tl.outPoint = tout;
            tl.property("Position").setValue([W / 2, ty]);
            fitText(tl, W * 0.9); // measured fit, never leave the safe zone

            // per-character reveal: chars rise + fade in as the selector sweeps
            var anims = tl.property("ADBE Text Properties").property("ADBE Text Animators");
            var an = anims.addProperty("ADBE Text Animator");
            an.property("ADBE Text Animator Properties").addProperty("ADBE Text Opacity").setValue(0);
            an.property("ADBE Text Animator Properties").addProperty("ADBE Text Position 3D").setValue([0, 26, 0]);
            var sel = an.property("ADBE Text Selectors").addProperty("ADBE Text Selector");
            try {
                sel.property("ADBE Text Range Advanced").property("ADBE Text Selector Shape").setValue(2); // ramp up = soft edge
            } catch (e) { }
            var st = sel.property("ADBE Text Percent Start");
            st.setValueAtTime(tin, 0);
            st.setValueAtTime(tin + 0.75, 100);
            try {
                var e1 = [new KeyframeEase(0, 66)];
                st.setTemporalEaseAtKey(1, e1, e1);
                st.setTemporalEaseAtKey(2, e1, e1);
            } catch (e) { }

            var top = tl.property("Opacity");
            top.setValueAtTime(tout - 0.35, 100);
            top.setValueAtTime(tout, 0);
            var ds = tl.property("ADBE Effect Parade").addProperty("ADBE Drop Shadow");
            ds.property("ADBE Drop Shadow-0002").setValue(60);  // opacity (0-255 scale uses 0-100 in UI; script takes 0-255? use modest)
            ds.property("ADBE Drop Shadow-0004").setValue(6);   // distance
            ds.property("ADBE Drop Shadow-0005").setValue(12);  // softness

            // orange accent under the line
            var accW = Math.round(W * 0.14), accH = Math.max(5, Math.round(H * 0.006));
            var accY = ty + Math.round(W * 0.028) + (lineCount > 1 ? Math.round(fsize * 1.25) : 0);
            var acc = comp.layers.addSolid(ORANGE, "accent " + s, accW, accH, 1);
            acc.property("Anchor Point").setValue([accW / 2, accH / 2]);
            acc.property("Position").setValue([W / 2, accY]);
            acc.inPoint = tin + 0.15; acc.outPoint = tout;
            var asc = acc.property("Scale");
            asc.setValueAtTime(tin + 0.15, [0, 100]);
            asc.setValueAtTime(tin + 0.6, [100, 100]);
            easeKeys(asc, 1, 2);
            var aop = acc.property("Opacity");
            aop.setValueAtTime(tout - 0.35, 100);
            aop.setValueAtTime(tout, 0);
            overlays.push(tl); overlays.push(acc);
        }
        t += shot.dur;
    }

    // bottom legibility gradient — sits above footage; text/accents re-raised above it below
    var grad = comp.layers.addSolid([0, 0, 0], "bottom grade", W, H, 1);
    var m = grad.property("ADBE Mask Parade").addProperty("ADBE Mask Atom");
    var shape = new Shape();
    shape.vertices = [[0, H * 0.62], [W, H * 0.62], [W, H], [0, H]];
    shape.closed = true;
    m.property("ADBE Mask Shape").setValue(shape);
    m.property("ADBE Mask Feather").setValue([220, 220]);
    grad.property("Opacity").setValue(38);
    grad.outPoint = t; // ends before end card

    // vignette (top layer, subtle)
    var vig = comp.layers.addSolid([0, 0, 0], "vignette", W, H, 1);
    var vm = vig.property("ADBE Mask Parade").addProperty("ADBE Mask Atom");
    var vs = new Shape();
    var inX = W * 0.10, inY = H * 0.10;
    vs.vertices = [[inX, inY], [W - inX, inY], [W - inX, H - inY], [inX, H - inY]];
    vs.closed = true;
    vm.property("ADBE Mask Shape").setValue(vs);
    vm.maskMode = MaskMode.SUBTRACT;
    vm.property("ADBE Mask Feather").setValue([320, 320]);
    vig.property("Opacity").setValue(22);
    vig.outPoint = total;

    // raise shot text + accents above the gradient/vignette
    for (var o = 0; o < overlays.length; o++) overlays[o].moveToBeginning();

    // ---- end card ----
    if (job.endCard) {
        var ec = job.endCard, ecIn = t;
        var card = comp.layers.addSolid(NAVY, "end card", W, H, 1);
        card.inPoint = ecIn; card.outPoint = total;
        var cop = card.property("Opacity");
        cop.setValueAtTime(ecIn, 0);
        cop.setValueAtTime(ecIn + XFADE, 100);

        var logoFile = new File(PIPE + "/AE_Templates/CARR_White_Logo.png");
        if (logoFile.exists) {
            var lf = app.project.importFile(new ImportOptions(logoFile));
            var ll = comp.layers.add(lf);
            ll.inPoint = ecIn + 0.2; ll.outPoint = total;
            var lsc = (W * 0.42 / lf.width) * 100;
            var lprop = ll.property("Scale");
            lprop.setValueAtTime(ecIn + 0.2, [lsc * 0.92, lsc * 0.92]);
            lprop.setValueAtTime(ecIn + 0.75, [lsc, lsc]);
            easeKeys(lprop, 1, 2);
            ll.property("Position").setValue([W / 2, H * 0.40]);
            var lop = ll.property("Opacity");
            lop.setValueAtTime(ecIn + 0.2, 0);
            lop.setValueAtTime(ecIn + 0.65, 100);
        }

        // orange rule
        var rw = Math.round(W * 0.20), rh = Math.max(5, Math.round(H * 0.006));
        var rule = comp.layers.addSolid(ORANGE, "ec rule", rw, rh, 1);
        rule.property("Anchor Point").setValue([rw / 2, rh / 2]);
        rule.property("Position").setValue([W / 2, H * 0.52]);
        rule.inPoint = ecIn + 0.5; rule.outPoint = total;
        var rsc = rule.property("Scale");
        rsc.setValueAtTime(ecIn + 0.5, [0, 100]);
        rsc.setValueAtTime(ecIn + 0.95, [100, 100]);
        easeKeys(rsc, 1, 2);

        function ecText(str, chain, size, color, y, delay) {
            var tl = comp.layers.addText(str);
            setFont(tl, chain);
            var td = tl.property("Source Text").value;
            td.fontSize = size; td.fillColor = color;
            td.justification = ParagraphJustification.CENTER_JUSTIFY;
            td.tracking = 60;
            tl.property("Source Text").setValue(td);
            tl.property("Position").setValue([W / 2, y]);
            tl.inPoint = ecIn + delay; tl.outPoint = total;
            var op = tl.property("Opacity");
            op.setValueAtTime(ecIn + delay, 0);
            op.setValueAtTime(ecIn + delay + 0.45, 100);
        }
        ecText(ec.name, FONT_HEAD, Math.round(W * 0.055), [1, 1, 1], H * 0.60, 0.65);
        ecText(ec.title, FONT_BODY, Math.round(W * 0.030), ORANGE, H * 0.655, 0.8);
        if (ec.tagline) ecText(ec.tagline, FONT_BODY, Math.round(W * 0.026), [0.85, 0.88, 0.95], H * 0.74, 0.95);
    }

    var aep = new File(PIPE + "/AE_Templates/StockClip_last_generated.aep");
    app.project.save(aep);

    var rqi = app.project.renderQueue.items.add(comp);
    var om = rqi.outputModule(1);
    var tries = ["Lossless", "High Quality"];
    var applied = null;
    for (var q = 0; q < tries.length; q++) {
        try { om.applyTemplate(tries[q]); applied = tries[q]; break; } catch (e) { }
    }
    if (!applied) fail("no output template; have: " + om.templates.join(" | "));
    om.file = new File(job.outPath);
    log("rendering (" + applied + ") -> " + job.outPath);
    app.project.renderQueue.render();
    log("DONE: " + job.outPath);
    } catch (e) {
        log("ERROR: " + e.toString() + " (line " + (e.line || "?") + ")");
    }
})();
