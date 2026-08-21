/*
 * Door check-in by camera.
 *
 * The app issues a QR on every ticket and, until now, could not read one — a
 * door person had to squint at a phone and type twelve characters per person,
 * in a queue, in the dark. This closes that.
 *
 * Uses the browser's own BarcodeDetector. No library, for three reasons: the
 * page has to work on a bundle that ran out on the walk over, a scanning
 * library is a hundred kilobytes of WASM, and the one thing worse than typing
 * codes is a scanner that fails to load and leaves nothing in its place.
 *
 * Where BarcodeDetector is missing — Safari, Firefox — nothing is rendered at
 * all and the typed field stays exactly as it was. A dead camera button is
 * worse than no camera button.
 */
(function () {
  "use strict";

  var root = document.querySelector("[data-scanner]");
  if (!root) return;

  var supported =
    "BarcodeDetector" in window &&
    navigator.mediaDevices &&
    typeof navigator.mediaDevices.getUserMedia === "function";

  if (!supported) return;

  var startButton = root.querySelector("[data-scanner-start]");
  var stopButton = root.querySelector("[data-scanner-stop]");
  var stage = root.querySelector("[data-scanner-stage]");
  var video = root.querySelector("[data-scanner-video]");
  var status = root.querySelector("[data-scanner-status]");
  var input = document.querySelector("[data-scanner-target]");
  var form = input && input.form;

  if (!startButton || !video || !input || !form) return;

  // The whole panel is hidden in markup and revealed here, so the button only
  // exists on a browser that can actually honour it.
  root.hidden = false;

  // Same shape as generate_ticket_code(), and the same alphabet — a code is
  // pulled out of whatever the QR encoded, which is the ticket's URL.
  var CODE = /VE-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}-[ABCDEFGHJKLMNPQRSTUVWXYZ23456789]{4}/;

  var stream = null;
  var detector = null;
  var timer = null;
  var lastCode = "";
  var lastAt = 0;

  function say(message, tone) {
    if (!status) return;
    status.textContent = message;
    status.className =
      "mt-3 text-center text-xs " +
      (tone === "bad" ? "text-rose-600 dark:text-rose-400" : "text-ink-subtle");
  }

  function stop() {
    if (timer) {
      clearInterval(timer);
      timer = null;
    }
    if (stream) {
      stream.getTracks().forEach(function (track) {
        track.stop();
      });
      stream = null;
    }
    video.srcObject = null;
    stage.hidden = true;
    startButton.hidden = false;
    if (stopButton) stopButton.hidden = true;
  }

  function accept(value) {
    var match = CODE.exec((value || "").toUpperCase());
    if (!match) {
      say("That code isn't one of ours.", "bad");
      return;
    }

    var code = match[0];
    var now = Date.now();

    // One ticket held in front of the lens is dozens of frames. Without this
    // the same person is submitted repeatedly and the second attempt reports
    // them as already checked in — which reads, at a door, as a rejection.
    if (code === lastCode && now - lastAt < 4000) return;
    lastCode = code;
    lastAt = now;

    input.value = code;
    say("Found " + code + " — checking in…");
    stop();

    // A real submit, so this goes through exactly the same view, permissions
    // and duplicate handling as a typed code. The scanner is a faster way to
    // fill the field in, not a second way to check somebody in.
    if (typeof form.requestSubmit === "function") {
      form.requestSubmit();
    } else {
      form.submit();
    }
  }

  function scan() {
    if (!detector || video.readyState !== video.HAVE_ENOUGH_DATA) return;

    detector
      .detect(video)
      .then(function (codes) {
        if (codes && codes.length) accept(codes[0].rawValue);
      })
      .catch(function () {
        // A single failed frame is not worth reporting; the next one is 250ms
        // away. Only losing the camera entirely is worth interrupting for.
      });
  }

  function start() {
    say("Starting the camera…");
    startButton.hidden = true;

    try {
      detector = new window.BarcodeDetector({ formats: ["qr_code"] });
    } catch (error) {
      say("This browser can't read QR codes. Type the code instead.", "bad");
      startButton.hidden = false;
      return;
    }

    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" } })
      .then(function (media) {
        stream = media;
        video.srcObject = media;
        stage.hidden = false;
        if (stopButton) stopButton.hidden = false;
        say("Point it at the QR on their ticket.");
        return video.play();
      })
      .then(function () {
        // Four frames a second. Faster drains a phone that has to last the
        // whole night at a door with no plug.
        timer = setInterval(scan, 250);
      })
      .catch(function () {
        say("No camera access. Allow it in your browser, or type the code.", "bad");
        startButton.hidden = false;
        stop();
      });
  }

  startButton.addEventListener("click", start);
  if (stopButton) stopButton.addEventListener("click", stop);

  // Let go of the camera when the page goes away. A held camera keeps the
  // indicator light on and the battery draining, and on some Androids the next
  // page cannot open it at all.
  window.addEventListener("pagehide", stop);
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop();
  });
})();
