# Veil — experimental student MVP

Veil experiments with perturbations aimed at the Stable Diffusion v1.5 VAE and adds an unauthenticated no-AI-manipulation signal. It does not guarantee protection. A preliminary single-photo SD v1.5 test showed visible distortion, but Meta edited a processed photo successfully. The seal can be forged or lost, does not prove identity or consent, and is not legally binding by itself.

## Your files
- `index.html`: your Greek-gate website.
- `css/style.css`: website appearance.
- `js/app.js`: website interaction and server calls.
- `app.py`: the server, shield and seal in one Python file.
- `requirements-colab.txt`: the Colab dependencies; PyTorch, NumPy and OpenCV come from Colab.
- `Start_in_Colab.ipynb`: starter notebook.

## Try the demo
1. Download this ZIP; open `Start_in_Colab.ipynb` from Google Colab and select a T4 GPU.
2. Run the notebook cells individually. Upload the same ZIP when asked, install the libraries, start the server and open its temporary link.
3. Paste the private access key into the page; use a photo you have permission to use. The first run needs model downloads.

The server keeps queued photos and results in process memory. A background task expires completed results roughly 15 minutes after finishing; process interruption and external services are outside that guarantee. Restarting the server clears memory and all job IDs. Never assume sensitive uploads are safe on this experimental setup. Logs and tunnel infrastructure can contain metadata. The result can be downloaded while the session runs.

## GitHub
Create a **private** repository, unzip the project, and upload the contents of the `veil_mvp` folder. Never upload your access key, a live URL, private photos, notebook outputs or downloaded results. Avoid committing the current session notebook after keys are printed: clear cell outputs first.

## Before public use
This is a consolidated prototype, not a security-audited service. GPU execution and browser flow in this version remain unverified. The Colab package versions are an experimental workaround and can conflict with preinstalled packages; the model and cloudflared are downloaded externally. Audit and update dependencies, control uploads at the proxy, use appropriate authentication and production rate limits, pin and verify downloads, and review privacy and UAE law with a qualified mentor or lawyer. The temporary tunnel and in-memory job state are not durable hosting. The public `/verify` endpoint can return false positives. The website and backend are served together; this is not the same API as your teammate's Space.
