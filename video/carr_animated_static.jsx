// carr_animated_static.jsx — CARR animated-static builder
// Takes a static you already run, as its authored LAYERS, and rebuilds it as a
// stop-motion build: each element jumps into place pose to pose, one frame of
// wobble on every landing, locked camera, no narration. The last second holds on
// the finished static, so the end frame IS the original graphic.
//
// Reads Scripts/animstatic-job.json (written by make-animated-static.sh):
//   { compName, width, height, fps, duration, stepFrames, holdTail,
//     layers: [ { src, beat, from: "below|above|left|right|scale", drift } ],
//     outPath }
// Renders lossless; ffmpeg does the H.264 finish, the audio, and the GIF.
//
// Why HOLD keyframes and not Posterize Time: posterizing the whole comp steps
// everything including the render's own sampling and fights the encoder. Hard
// HOLD keys on each transform give the same pose-to-pose read, per layer,
// predictably. That is the entire stop-motion effect here.

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
    var LOG = new File(PIPE + "/Scripts/animstatic-log.txt");
    function log(msg) { LOG.open("a"); LOG.writeln(new Date().toTimeString().substr(0, 8) + "  " + msg); LOG.close(); }
    function fail(msg) { log("FAIL: " + msg); throw new Error(msg); }

    var jobFile = new File(PIPE + "/Scripts/animstatic-job.json");
    if (!jobFile.exists) fail("job file missing");
    jobFile.open("r"); var job = eval("(" + jobFile.read() + ")"); jobFile.close();

    try {
        var W = job.width, H = job.height, FPS = job.fps || 30;
        var TOTAL = job.duration || 6;
        var STEP = (job.stepFrames || 3) / FPS;   // one stop-motion "pose" = 3 frames = 10 poses/sec
        var NAVY = [0 / 255, 47 / 255, 108 / 255];

        log("start: " + job.layers.length + " layers, " + W + "x" + H + ", " +
            TOTAL + "s, step " + (job.stepFrames || 3) + "f");

        app.newProject();
        var comp = app.project.items.addComp(job.compName || "CARR_AnimatedStatic",
            W, H, 1, TOTAL, FPS);
        comp.bgColor = NAVY;

        // Hold every key on a property: the value jumps at each key and sits there.
        // This is what makes it read as handmade stop-motion rather than a smooth tween.
        function holdAll(prop) {
            for (var k = 1; k <= prop.numKeys; k++) {
                try { prop.setInterpolationTypeAtKey(k, KeyframeInterpolationType.HOLD,
                                                     KeyframeInterpolationType.HOLD); } catch (e) { }
            }
        }

        // Build order is the array order, and it is also the STACKING order.
        // comp.layers.add() always inserts at the top of the stack, so adding
        // forward leaves layer 1 (the background plate) at the bottom and the
        // last element on top. Adding in reverse buries everything under the
        // opaque background — that was the first render's failure.
        for (var i = 0; i < job.layers.length; i++) {
            var spec = job.layers[i];
            var f = new File(spec.src);
            if (!f.exists) fail("layer art missing: " + spec.src);

            var art = app.project.importFile(new ImportOptions(f));
            var lyr = comp.layers.add(art);
            lyr.name = spec.name || ("layer " + (i + 1));

            // Every layer PNG is exported full-canvas, so a naive scale would
            // shrink the element toward the CANVAS center — which drags a logo
            // sitting near the bottom edge halfway up the frame on its way in.
            // The driver measures each layer's alpha bounding box and passes its
            // center; anchoring there means scale and rotation happen about the
            // element itself. Position is set to the same point, so the resting
            // pose is pixel-identical to the source static.
            var cx = spec.anchor ? spec.anchor[0] : W / 2;
            var cy = spec.anchor ? spec.anchor[1] : H / 2;
            lyr.property("Anchor Point").setValue([cx, cy]);

            var from = spec.from || "below";

            // The background plate is the canvas. It never moves, scales or
            // tilts — anything else shows empty comp around its edges.
            if (from === "plate") {
                lyr.inPoint = 0;
                lyr.outPoint = TOTAL;
                lyr.property("Position").setValue([cx, cy]);
                log("  layer " + (i + 1) + " '" + lyr.name + "' plate (static)");
                continue;
            }

            var drift = spec.drift || Math.round(H * 0.045);
            var dx = 0, dy = 0;
            if (from === "below") dy = drift;
            else if (from === "above") dy = -drift;
            else if (from === "left") dx = -drift;
            else if (from === "right") dx = drift;

            var beat = spec.beat;
            lyr.inPoint = beat;
            lyr.outPoint = TOTAL;

            // --- the landing: 4 poses, all HOLD ---
            //  p0  offset, small, invisible-to-visible on the same frame
            //  p1  overshoot past the mark (the "thrown into place" pose)
            //  p2  one frame of wobble back the other way
            //  p3  final resting pose = the source static exactly
            var p0 = beat, p1 = beat + STEP, p2 = beat + STEP * 2, p3 = beat + STEP * 3;

            var op = lyr.property("Opacity");
            op.setValueAtTime(Math.max(0, beat - STEP), 0);
            op.setValueAtTime(p0, 100);
            holdAll(op);

            var pos = lyr.property("Position");
            pos.setValueAtTime(p0, [cx + dx, cy + dy]);
            pos.setValueAtTime(p1, [cx - dx * 0.28, cy - dy * 0.28]);   // overshoot
            pos.setValueAtTime(p2, [cx + dx * 0.10, cy + dy * 0.10]);   // wobble back
            pos.setValueAtTime(p3, [cx, cy]);                            // land true
            holdAll(pos);

            var sc = lyr.property("Scale");
            var s0 = (from === "scale") ? 82 : 94;
            sc.setValueAtTime(p0, [s0, s0]);
            sc.setValueAtTime(p1, [104, 104]);
            sc.setValueAtTime(p2, [98.5, 98.5]);
            sc.setValueAtTime(p3, [100, 100]);
            holdAll(sc);

            // A degree or two of rotation on the way in reads as handmade. The
            // final pose is always exactly 0 so the last frame is the real static.
            var rot = lyr.property("Rotation");
            var tilt = spec.tilt === undefined ? 1.6 : spec.tilt;
            if (tilt !== 0) {
                rot.setValueAtTime(p0, -tilt);
                rot.setValueAtTime(p1, tilt * 0.55);
                rot.setValueAtTime(p2, -tilt * 0.2);
                rot.setValueAtTime(p3, 0);
                holdAll(rot);
            }
            log("  layer " + (i + 1) + " '" + lyr.name + "' beat " + beat.toFixed(2) +
                "s from " + from);
        }

        var aep = new File(PIPE + "/AE_Templates/AnimatedStatic_last_generated.aep");
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
