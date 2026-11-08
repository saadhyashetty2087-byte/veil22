// Veil website logic. Talks to the same server that serves this page.
// Flow: pick photo -> POST /protect -> poll /jobs/<id> -> GET /download/<id> -> show before/after

const $ = (id) => document.getElementById(id);
const tokenInput = $("token");
const drop = $("drop"), fileInput = $("file"), preview = $("preview");
const go = $("go"), statusEl = $("status");
let chosenFile = null;

// remember the key for this tab only (cleared when the tab closes)
tokenInput.value = sessionStorage.getItem("veilToken") || "";
tokenInput.addEventListener("input", () => {
  sessionStorage.setItem("veilToken", tokenInput.value.trim());
  refreshButton();
});

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;               // textContent (not innerHTML) so server text can't inject HTML
  statusEl.classList.toggle("err", isError);
}

function refreshButton() {
  go.disabled = !(chosenFile && tokenInput.value.trim());
}

// ---------- choosing a photo ----------
function takeFile(f) {
  if (!f) return;
  if (!["image/png", "image/jpeg", "image/webp"].includes(f.type)) {
    return setStatus("Please choose a JPEG, PNG or WEBP photo.", true);
  }
  if (f.size > 10 * 1024 * 1024) {
    return setStatus("That photo is over 10 MB.", true);
  }
  chosenFile = f;
  preview.src = URL.createObjectURL(f);
  preview.hidden = false;
  setStatus("");
  refreshButton();
}
drop.addEventListener("click", () => fileInput.click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
fileInput.addEventListener("change", () => takeFile(fileInput.files[0]));
["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => takeFile(e.dataTransfer.files[0]));

// ---------- protecting ----------
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function api(path, opts = {}) {
  const headers = Object.assign({ Authorization: "Bearer " + tokenInput.value.trim() }, opts.headers || {});
  const res = await fetch(path, Object.assign({}, opts, { headers }));
  if (res.status === 401) throw new Error("Wrong access key.");
  if (res.status === 429) throw new Error("Too many requests. Wait a minute and try again.");
  return res;
}

go.addEventListener("click", async () => {
  go.disabled = true;
  $("result").hidden = true;
  try {
    const form = new FormData();
    form.append("file", chosenFile);
    form.append("strength", document.querySelector('input[name="strength"]:checked').value);

    setStatus("Sending your photo to the forge...");
    const res = await api("/protect", { method: "POST", body: form });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail || "The forge refused that photo.");
    }
    const { job_id } = await res.json();

    // poll until done (max about 10 minutes)
    let info = null;
    for (let i = 0; i < 300; i++) {
      await sleep(2000);
      const r = await api("/jobs/" + encodeURIComponent(job_id));
      if (!r.ok) throw new Error("Lost track of the job. Try again.");
      info = await r.json();
      if (info.status === "failed") throw new Error("Processing failed. Is the GPU running?");
      if (info.status === "done") break;
      setStatus(info.status === "queued" ? "Waiting in line..." : "Forging the shield... this can take a minute.");
    }
    if (!info || info.status !== "done") throw new Error("This is taking too long. Try again.");

    const dl = await api("/download/" + encodeURIComponent(job_id));
    if (!dl.ok) throw new Error("Could not fetch the result.");
    const blob = await dl.blob();
    const url = URL.createObjectURL(blob);

    $("imgBefore").src = preview.src;
    $("imgAfter").src = url;
    $("download").href = url;
    $("stats").textContent = "Largest change to any pixel: " + info.max_change + " out of 255. " +
      "Check the slider to judge visible quality yourself.";
    $("result").hidden = false;
    setStatus("Done. Your processed image is ready. Effectiveness is not guaranteed.");
    $("result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    setStatus(err.message || "Something went wrong.", true);
  } finally {
    refreshButton();
  }
});

// before / after slider: moves the clip edge of the protected image
$("slider").addEventListener("input", (e) => {
  $("imgAfter").style.clipPath = "inset(0 0 0 " + e.target.value + "%)";
});

// ---------- reading the seal ----------
const vfile = $("vfile"), vgo = $("vgo"), vres = $("vresult");
vfile.addEventListener("change", () => { vgo.disabled = !vfile.files[0]; vres.textContent = ""; });
vgo.addEventListener("click", async () => {
  vgo.disabled = true;
  vres.classList.remove("err");
  vres.textContent = "Reading...";
  try {
    const form = new FormData();
    form.append("file", vfile.files[0]);
    const res = await fetch("/verify", { method: "POST", body: form });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || "Could not read that file.");
    vres.textContent = body.veil_watermark_found
      ? "Veil seal found. The unauthenticated signal requests no AI manipulation (seal ID " + body.details.protection_id + ")."
      : "No Veil seal found. " + body.note;
  } catch (err) {
    vres.textContent = err.message;
    vres.classList.add("err");
  } finally {
    vgo.disabled = false;
  }
});

