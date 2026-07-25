// carr_lower_third.jsx — CARR branded lower-third generator
// Reads Scripts/lowerthird-job.json, builds the comp, renders a ProRes/Animation
// overlay with alpha to 03_Output. Driven by make-lower-third.sh via AppleScript
// DoScriptFile, or run manually from AE: File > Scripts > Run Script File.
//
// Brand: Navy #002F6C, Orange #F57F29 (accent only), white text.
// Job file format (written by the wrapper):
// { name: "Joe Bookout", title: "Healthcare Real Estate | CARR",
//   orientation: "horizontal"|"vertical", duration: 6, outPath: "...", logo: true }

(function () {
    // Allow script file writes (log + project save) without the prefs checkbox
    try {
        app.preferences.savePrefAsBool("Main Pref Section",
            "Pref_SCRIPTING_FILE_NETWORK_SECURITY", true, PREFType.PREF_Type_MACHINE_INDEPENDENT);
    } catch (e) { /* older pref API; continue */ }

    var PIPE;
    try { PIPE = Folder(new File($.fileName).parent.parent).fsName; } catch (e) { }
    if (!PIPE || !Folder(PIPE + "/Scripts").exists) {
        PIPE = Folder("~/Movies/CARR Video Pipeline").fsName;
    }
    var LOG = new File(PIPE + "/Scripts/lowerthird-log.txt");

    function log(msg) {
        LOG.open("a"); LOG.writeln(new Date().toTimeString().substr(0, 8) + "  " + msg); LOG.close();
    }

    function fail(msg) { log("FAIL: " + msg); throw new Error(msg); }

    // ---- read job ----
    var jobFile = new File(PIPE + "/Scripts/lowerthird-job.json");
    if (!jobFile.exists) fail("job file missing: " + jobFile.fsName);
    jobFile.open("r"); var jobSrc = jobFile.read(); jobFile.close();
    var job = eval("(" + jobSrc + ")"); // our own generated file

    var vertical = (job.orientation === "vertical");
    var W = vertical ? 1080 : 1920, H = vertical ? 1920 : 1080;
    var DUR = job.duration || 6, FPS = 29.97;

    var NAVY = [0 / 255, 47 / 255, 108 / 255];
    var ORANGE = [245 / 255, 127 / 255, 41 / 255];

    log("start: " + job.name + " / " + job.title + " / " + W + "x" + H);

    // ---- project + comp ----
    app.newProject();
    var comp = app.project.items.addComp("CARR_LowerThird", W, H, 1, DUR, FPS);

    // Layout constants — bar sits in the lower-left safe zone
    var base = Math.min(W, H);                     // short edge keeps proportions
    var barH = Math.round(base * 0.102);           // 110px at 1080
    var barW = Math.round(W * (vertical ? 0.86 : 0.40));
    var barX = Math.round(W * (vertical ? 0.07 : 0.055));
    var barY = Math.round(H * (vertical ? 0.80 : 0.845)); // top of bar
    var IN_END = 0.8, OUT_START = DUR - 0.7;

    function easeIn(prop, k1, k2) { // easy-ease both keys
        var e = [new KeyframeEase(0, 66)];
        try {
            prop.setTemporalEaseAtKey(k1, e, e);
            prop.setTemporalEaseAtKey(k2, e, e);
        } catch (err) { }
    }

    function fadeOut(layer) {
        var op = layer.property("Opacity");
        var v = op.value;
        op.setValueAtTime(OUT_START, v);
        op.setValueAtTime(OUT_START + 0.5, 0);
    }

    // ---- navy bar: scales open left-to-right ----
    var bar = comp.layers.addSolid(NAVY, "navy bar", barW, barH, 1);
    bar.property("Anchor Point").setValue([0, barH / 2]);
    bar.property("Position").setValue([barX, barY + barH / 2]);
    var bs = bar.property("Scale");
    bs.setValueAtTime(0, [0, 100]);
    bs.setValueAtTime(IN_END - 0.2, [100, 100]);
    easeIn(bs, 1, 2);
    fadeOut(bar);

    // ---- orange accent underline ----
    var accH = Math.max(6, Math.round(base * 0.0065));
    var acc = comp.layers.addSolid(ORANGE, "orange accent", barW, accH, 1);
    acc.property("Anchor Point").setValue([0, accH / 2]);
    acc.property("Position").setValue([barX, barY + barH + accH * 1.5]);
    var as_ = acc.property("Scale");
    as_.setValueAtTime(0.15, [0, 100]);
    as_.setValueAtTime(IN_END, [100, 100]);
    easeIn(as_, 1, 2);
    fadeOut(acc);

    // ---- text ----
    var FONTS = ["Montserrat-Bold", "Oswald-Bold", "HelveticaNeue-Bold"];
    function makeText(str, size, color, x, y, delay) {
        var tl = comp.layers.addText(str);
        var td = tl.property("Source Text").value;
        for (var i = 0; i < FONTS.length; i++) {
            td.font = FONTS[i];
            tl.property("Source Text").setValue(td);
            if (tl.property("Source Text").value.font === FONTS[i]) break;
        }
        td = tl.property("Source Text").value;
        td.fontSize = size; td.fillColor = color; td.applyStroke = false;
        td.justification = ParagraphJustification.LEFT_JUSTIFY;
        tl.property("Source Text").setValue(td);
        tl.property("Position").setValue([x, y]);
        var op = tl.property("Opacity");
        op.setValueAtTime(delay, 0);
        op.setValueAtTime(delay + 0.4, 100);
        var pos = tl.property("Position");
        pos.setValueAtTime(delay, [x - 40, y]);
        pos.setValueAtTime(delay + 0.5, [x, y]);
        easeIn(pos, 1, 2);
        fadeOut(tl);
        return tl;
    }

    var pad = Math.round(barH * 0.22);
    var nameSize = Math.round(barH * 0.40);
    var titleSize = Math.round(barH * 0.24);
    makeText(job.name, nameSize, [1, 1, 1],
        barX + pad, barY + pad + nameSize, 0.35);
    makeText(job.title, titleSize, ORANGE,
        barX + pad, barY + barH - pad * 0.6, 0.5);

    // ---- logo (white CARR mark, right-aligned on the bar) ----
    if (job.logo !== false) {
        var logoFile = new File(PIPE + "/AE_Templates/CARR_White_Logo.png");
        if (logoFile.exists) {
            var io = new ImportOptions(logoFile);
            var footage = app.project.importFile(io);
            var ll = comp.layers.add(footage);
            var targetH = barH * 0.5;
            var sc = (targetH / footage.height) * 100;
            ll.property("Scale").setValue([sc, sc]);
            var lw = footage.width * sc / 100;
            ll.property("Position").setValue([barX + barW - lw / 2 - pad, barY + barH / 2]);
            var lop = ll.property("Opacity");
            lop.setValueAtTime(0.5, 0);
            lop.setValueAtTime(1.0, 100);
            fadeOut(ll);
        } else { log("logo missing, skipped: " + logoFile.fsName); }
    }

    // ---- save + render ----
    var aep = new File(PIPE + "/AE_Templates/LowerThird_last_generated.aep");
    app.project.save(aep);

    var rqi = app.project.renderQueue.items.add(comp);
    var om = rqi.outputModule(1);
    var applied = null;
    var tries = ["Apple ProRes 4444 with alpha", "Lossless with Alpha", "High Quality with Alpha"];
    for (var t = 0; t < tries.length; t++) {
        try { om.applyTemplate(tries[t]); applied = tries[t]; break; } catch (e) { }
    }
    if (!applied) fail("no alpha output template found; available: " + om.templates.join(" | "));
    om.file = new File(job.outPath);
    log("rendering with template: " + applied + " -> " + job.outPath);
    app.project.renderQueue.render();
    log("DONE: " + job.outPath);
})();
