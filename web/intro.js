// Intro: a dot globe that flies down to Cottbus as you scroll, then hands over to the map.
// It sits on top of the page; when it ends (or "Skip" is pressed) it fades away and the map zooms in.
(function () {
  const intro = document.getElementById("intro");
  if (!intro) return;

  const COTTBUS = { lat: 51.786, lng: 14.417 };
  const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;

  // A shared link (#d=...) should open the view straight away, without the intro
  if (location.hash.length > 1 || typeof Globe !== "function") {
    intro.remove();
    return;
  }

  const globeEl = document.getElementById("globe");
  const title = intro.querySelector(".intro-title");
  const cue = intro.querySelector(".intro-cue");
  document.documentElement.classList.add("intro-on");
  intro.focus({ preventScroll: true });

  // Camera stops: [scroll progress, where the camera looks]
  const PATH = [
    [0.4, { lat: 46, lng: 11, altitude: 1.75 }],
    [0.68, { lat: 51, lng: 14, altitude: 0.8 }],
    [0.92, { lat: COTTBUS.lat, lng: COTTBUS.lng, altitude: 0.22 }],
  ];

  const clamp = (v, a = 0, b = 1) => Math.min(b, Math.max(a, v));
  const lerp = (a, b, t) => a + (b - a) * t;
  const smooth = (a, b, x) => { const t = clamp((x - a) / (b - a)); return t * t * (3 - 2 * t); };
  const ease = (t) => (t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2);
  const lerpLng = (a, b, t) => a + ((((b - a) + 540) % 360) - 180) * t;

  // ---------- the globe ----------
  let world = null;
  fetch("data/globe-dots.json")
    .then((r) => r.json())
    .then((dots) => {
      if (done) return; // skipped before the globe finished loading
      world = Globe({ animateIn: !reduce })(globeEl)
        .width(globeEl.clientWidth).height(globeEl.clientHeight)
        .backgroundColor("rgba(0,0,0,0)")
        .enablePointerInteraction(false)
        .showAtmosphere(true).atmosphereColor("#B69CFF").atmosphereAltitude(0.22)
        // land as glowing dots, Germany in pink
        .particlesData([
          { pts: dots.world, color: "rgba(182,156,255,0.95)", size: 0.46 },
          { pts: dots.europe, color: "rgba(196,176,255,1)", size: 0.23 },
          { pts: dots.germany, color: "#FF8FB1", size: 0.27 },
        ])
        .particlesList((d) => d.pts)
        .particleLat((p) => p[0]).particleLng((p) => p[1]).particleAltitude(0.003)
        .particlesColor((d) => d.color).particlesSize((d) => d.size).particlesSizeAttenuation(true)
        // a golden pulse on the lake
        .ringsData([COTTBUS]).ringColor(() => (t) => `rgba(255,211,110,${1 - t})`)
        .ringMaxRadius(1.4).ringPropagationSpeed(0.9).ringRepeatPeriod(1300)
        .pointsData([COTTBUS]).pointColor(() => "#FFD36E").pointAltitude(0.012).pointRadius(0.09);
      const material = world.globeMaterial();
      material.color.set("#1C1650");
      if (material.emissive) { material.emissive.set("#16113F"); material.emissiveIntensity = 0.7; }
      const controls = world.controls();
      controls.autoRotate = !reduce;
      controls.autoRotateSpeed = 0.55;
      controls.enableZoom = false;
      controls.enablePan = false;
      world.pointOfView({ lat: 24, lng: -18, altitude: 2.6 }, 0);
      addEventListener("resize", () => world.width(globeEl.clientWidth).height(globeEl.clientHeight));
    })
    .catch(() => finish(true)); // no globe: just show the site

  // ---------- scroll drives the camera ----------
  const progress = () => clamp(intro.scrollTop / (intro.scrollHeight - intro.clientHeight));
  let target = 0, p = 0, start = null, done = false, last = performance.now();
  intro.addEventListener("scroll", () => (target = progress()), { passive: true });

  function cameraAt(pp) {
    let from = { p: 0, pov: start };
    for (const [stop, pov] of PATH) {
      if (pp <= stop) {
        const t = ease(clamp((pp - from.p) / (stop - from.p)));
        return { lat: lerp(from.pov.lat, pov.lat, t), lng: lerpLng(from.pov.lng, pov.lng, t), altitude: lerp(from.pov.altitude, pov.altitude, t) };
      }
      from = { p: stop, pov };
    }
    return PATH[PATH.length - 1][1];
  }

  function tick(now) {
    if (done) return;
    // smooth follow that takes the same time on fast and slow computers (~0.2 s)
    const dt = Math.min(0.25, (now - last) / 1000);
    last = now;
    p = reduce ? target : p + (target - p) * (1 - Math.exp(-dt / 0.2));
    if (world) {
      const controls = world.controls();
      if (p < 0.004) {
        if (start) { controls.autoRotate = !reduce; start = null; }
      } else {
        if (!start) { start = world.pointOfView(); controls.autoRotate = false; }
        world.pointOfView(cameraAt(p), 0);
      }
    }
    // globe starts beside the title, then moves to the centre
    const side = 1 - smooth(0.04, 0.3, p);
    globeEl.style.transform = innerWidth >= 760
      ? `translateX(${(side * innerWidth * 0.13).toFixed(1)}px)`
      : `translateY(${(side * innerHeight * 0.17).toFixed(1)}px)`;
    const out = smooth(0, 0.12, p);
    title.style.opacity = 1 - out;
    title.style.transform = `translateY(${-60 * out}px)`;
    cue.style.opacity = 1 - smooth(0, 0.06, p);
    if (target > 0.97 && p > 0.93) finish(false);
    else requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  // ---------- hand over to the map ----------
  function finish(instant) {
    if (done) return;
    done = true;
    document.documentElement.classList.remove("intro-on");
    intro.classList.add("leaving");
    setTimeout(() => {
      // stop the 3D rendering for good, so it doesn't keep using the computer in the background
      if (world) {
        world.pauseAnimation();
        if (world._destructor) world._destructor();
        world = null;
      }
      intro.remove();
    }, instant ? 0 : 900);
    // the map zooms from the region down onto the lake
    if (typeof map !== "undefined" && map && state.bounds && !instant) {
      const lake = L.latLngBounds(state.bounds);
      map.setView(lake.getCenter(), 9, { animate: false });
      map.flyToBounds(lake, { padding: [24, 24], duration: reduce ? 0 : 2.2 });
    }
  }
  intro.querySelector(".intro-skip").addEventListener("click", () => finish(false));
  addEventListener("keydown", (e) => { if (e.key === "Escape") finish(false); });
})();
