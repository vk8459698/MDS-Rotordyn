# Deploying the web interface

Two routes:

| | Hugging Face Spaces | Windows EC2 instance |
| --- | --- | --- |
| Effort | Push a git remote | Install Python, open two firewalls, register a service |
| Cost | Free | Billed per running hour, Windows licence included |
| Public URL | Yes, immediately | Only what you expose yourself |
| Can read ADRE Sxp files | No | Yes, eventually |

Spaces is the right way to get something in front of people **today**. EC2 is
where this has to end up, because native ADRE Sxp file support needs Windows
and the application should sit on the same machine as the data it opens.

---

# Part 1 — Hugging Face Spaces

Spaces offers three SDKs, and **Docker requires a paid plan while Gradio does
not**. So the Space runs `app.py`, a Gradio front end that renders exactly the
same Plotly figures as the Dash application, driven by the same `ViewSettings`
and the same `PolarModel`. Only the widgets differ.

The `Dockerfile` stays in the repository — it is still the cleanest way to run
this on EC2, where Docker costs nothing.

> **Before you push, decide whether the data should be public.** The bundled
> `CT2B_*.csv` exports are real measurements from a customer's machine, and a
> public Space publishes them along with the code. Choose **Private** when
> creating the Space unless you have been told the data can be shared. Private
> Spaces are free, work identically, and can still be shared with colleagues.
>
> The alternative is to leave the exports out. `app.py` handles that: the
> sample-data button reports that no data is bundled instead of failing, and
> the interface simply requires an upload.

### 1. Create the Space

At <https://huggingface.co/new-space>, choose **Gradio** as the SDK and the
**Blank** template. Pick **Private** visibility. Leave the hardware on the free
CPU tier — 2 vCPU and 16 GB is far more than this needs.

### 2. Authenticate

```bash
python -m pip install huggingface_hub
hf auth login
```

Paste an access token with **write** permission from
<https://huggingface.co/settings/tokens>.

### 3. Push

```bash
git remote add space https://huggingface.co/spaces/<username>/<space-name>
git push space main --force
```

The force is needed only on the first push, because Spaces creates the
repository with its own `README.md` and yours has to replace it.

Your `README.md` already carries the configuration block Spaces reads:

```yaml
sdk: gradio
app_file: app.py
```

No `sdk_version` is pinned, so Spaces installs its current default Gradio.
`app.py` avoids the handful of APIs that moved between Gradio 5 and 6, so
either works.

The build takes a few minutes. Watch it under the **Logs** tab; when it
finishes, the application appears on the **App** tab and at
`https://huggingface.co/spaces/<username>/<space-name>`.

### What to expect on the free tier

* The Space **sleeps after about 48 hours** with no visitors and takes a few
  seconds to wake. Fine for demos, not for anything anyone depends on.
* Storage is **ephemeral**. Uploaded files live in memory for the session and
  do not survive a restart, which is how the application already behaves.
* Each visitor gets their own `gr.State`, so two people using the Space at the
  same time never see each other's data.
* `requirements.txt` is shared with the rest of the project, so the Space also
  installs Dash, waitress and Matplotlib that it does not use. That costs about
  a minute of build time and keeps one dependency list to maintain. A test
  asserts `app.py` never imports Dash, so the two front ends cannot quietly
  become entangled.

### Running the Gradio interface locally

```powershell
python app.py          # http://127.0.0.1:7860
```

### Updating it

```bash
git push space main
```

---

# Part 2 — The Windows EC2 instance

The one thing that does not carry over from a Linux deployment is **gunicorn**,
which has no Windows support. `waitress` replaces it and is a real production
WSGI server.

Once Docker is installed on the instance, the same image works there too:

```powershell
docker build -t rotordyn .
docker run -d -p 8050:7860 --restart unless-stopped rotordyn
```

The rest of this section covers running it directly, without Docker.

---

## 1. Prepare the instance

Install [Python 3.11 or newer](https://www.python.org/downloads/windows/),
ticking **Add python.exe to PATH** in the installer, and
[Git for Windows](https://git-scm.com/download/win). Then, in PowerShell:

```powershell
cd C:\
git clone https://github.com/vk8459698/rotordyn.git RotorDyn
cd C:\RotorDyn
python -m pip install -r requirements.txt
python -m pytest -q
```

The test suite passing is the check that the box is set up correctly. It needs
no display and no ADRE installation.

## 2. Run it

```powershell
python main.py --web 8050 --production --host 0.0.0.0
```

`--production` serves through waitress instead of Flask's development server.
`--host 0.0.0.0` is what makes it reachable from other machines; the default of
`127.0.0.1` only accepts connections from the instance itself.

Equivalently, straight through the WSGI entry point:

```powershell
waitress-serve --listen=0.0.0.0:8050 wsgi:server
```

One waitress process serves every request from a thread pool, so the cache of
uploaded runs is shared across requests and no sticky-session handling is
needed.

## 3. Open the port — in both firewalls

A Windows EC2 instance has two independent firewalls, and traffic has to pass
both. Forgetting the second is the usual reason a correctly running server
appears unreachable.

**EC2 security group**, in the AWS console: add an inbound rule for TCP 8050.

**Windows Firewall**, on the instance:

```powershell
New-NetFirewallRule -DisplayName "RotorDyn viewer" -Direction Inbound `
    -Protocol TCP -LocalPort 8050 -Action Allow
```

### Restrict the source, and do the same for RDP

> The application has **no login and no HTTPS**. Anyone who can reach port 8050
> can use it and can see any data that has been uploaded to it.

Set the source on the security group rule to your office or VPN address range,
never `0.0.0.0/0`. The same goes for RDP on port 3389 — a Windows instance with
RDP open to the internet starts getting brute-forced within hours of launch, so
check that rule too while you are in there.

If this ever needs to be reachable from outside a trusted network, put an
Application Load Balancer with an ACM certificate in front of it and add
authentication. That is a separate piece of work; do not skip it by leaving the
port open.

## 4. Keep it running

Run as a scheduled task so the server survives a reboot and does not stop when
you close your RDP session. Adjust the two paths to match the instance:

```powershell
$action = New-ScheduledTaskAction -Execute "C:\Program Files\Python311\python.exe" `
    -Argument "main.py --web 8050 --production --host 0.0.0.0" `
    -WorkingDirectory "C:\RotorDyn"
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" `
    -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit 0

Register-ScheduledTask -TaskName "RotorDyn" -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings
Start-ScheduledTask -TaskName "RotorDyn"
```

Check on it, or stop it, with:

```powershell
Get-ScheduledTaskInfo -TaskName "RotorDyn"
Stop-ScheduledTask -TaskName "RotorDyn"
```

## 5. Cost

A Windows instance bills at a higher hourly rate than Linux because the licence
is included, and it bills for every hour it is *running* whether or not anyone
is using it.

For an internal tool that nobody touches overnight or at weekends, stopping the
instance outside working hours cuts the bill by roughly two thirds. AWS
Instance Scheduler does this, or a scheduled Lambda calling `StopInstances` and
`StartInstances`. Storage is still billed while an instance is stopped, but
that is a small fraction of the total.

## 6. Updating

```powershell
cd C:\RotorDyn
git pull
python -m pip install -r requirements.txt
python -m pytest -q
Stop-ScheduledTask -TaskName "RotorDyn"; Start-ScheduledTask -TaskName "RotorDyn"
```

---

## Troubleshooting

| Symptom | Cause |
| --- | --- |
| `ERR_ADDRESS_INVALID` on `http://0.0.0.0:8050` | `0.0.0.0` is a bind address, not a destination. Browse to `http://localhost:8050` instead |
| Works on the instance, not from your laptop | Bound to `127.0.0.1`; needs `--host 0.0.0.0` |
| Space wants a paid plan | The SDK is set to `docker`. Free accounts need `sdk: gradio` in `README.md` |
| Space build fails on `README.md` | The configuration block at the top of `README.md` was removed or edited |
| Space builds, then shows nothing | `app_file: app.py` must point at a file that calls `.launch()` |
| Server running, connection times out | Security group or Windows Firewall rule missing |
| Stops when you disconnect RDP | Started in a session instead of as a scheduled task |
| `source code string cannot contain null bytes` | A file got saved as UTF-16; run `python tools/normalise_encoding.py` |
| `--production needs waitress installed` | `python -m pip install -r requirements.txt` |
