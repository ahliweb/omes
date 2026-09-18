# OMES — Riset Mendalam dan Rencana Implementasi

> Status: baseline riset dan implementation plan
> Tanggal riset: 2026-09-18 (WIB)
> Repository: https://github.com/ahliweb/omes
> Owner: ahliweb / Unggul

## 1. Ringkasan eksekutif

OMES tidak akan mencoba memasang Omarchy resmi di Ubuntu Server atau Linux Mint. Omarchy resmi adalah distribusi berbasis Arch, Hyprland, dan Quickshell yang dipasang melalui ISO. OMES diposisikan sebagai **compatibility layer dan deployment toolkit yang terinspirasi workflow Omarchy**, dengan Hermes Agent sebagai komponen automation/agentic operation.

Keputusan produk awal:

- Ubuntu Server LTS: mode headless, Hermes-first, systemd, observability, Docker opsional, tanpa GUI secara default.
- Linux Mint: mode desktop tambahan dengan Hyprland/Wayland opsional; Cinnamon tetap menjadi fallback.
- Core OMES: installer idempotent, preflight, backup, rollback, package mapping, service management, diagnostics, dan dokumentasi.
- Hermes: instalasi per-user atau service-user, profile-safe `HERMES_HOME`, gateway Telegram opsional, dan konfigurasi secret terpisah.
- Semua fitur desktop bersifat modular karena Hyprland pada Ubuntu memiliki risiko dependency drift dan tidak cocok sebagai baseline server.

## 2. Temuan riset teknis

### 2.1 Upstream Omarchy

Omarchy menggabungkan Arch, Hyprland, Quickshell, Neovim, terminal/TUI workflow, development tools, AI CLI, themes, update channels, security defaults, dan system snapshots. Nilai produknya bukan sekadar daftar paket, tetapi integrasi opinionated, workflow keyboard-first, dan operasi yang terkoordinasi.

Fitur yang layak diadopsi sebagai konsep OMES:

- opinionated developer baseline;
- terminal/TUI-first workflow;
- mise untuk runtime/tool version management;
- agent sebagai first-class workflow;
- theme/configuration layer;
- update, diagnostics, backup, dan rollback yang eksplisit;
- security-by-default dan dokumentasi recovery.

Fitur yang tidak boleh disalin secara langsung sebagai asumsi:

- pacman/AUR/Arch mirror;
- Limine snapshot boot flow;
- konfigurasi ISO dan full-disk install;
- klaim bahwa Ubuntu/Mint mempunyai dependency/update guarantees yang sama.

### 2.2 Kelayakan Ubuntu dan Linux Mint

Ubuntu Server menyediakan autoinstall YAML yang divalidasi schema dan command list-nya berjalan sebagai root. Ini cocok untuk provisioning baseline, tetapi memperbesar risiko jika installer OMES memasukkan command remote atau command yang tidak idempotent. Karena itu autoinstall harus menjadi artefak terpisah, tervalidasi, dan tidak menjadi satu-satunya jalur instalasi.

Linux Mint cocok untuk desktop personal dan memiliki dukungan LTS, tetapi dependency desktop, GPU, compositor, display manager, portal, dan kernel harus diuji per versi. Mint diperlakukan sebagai Ubuntu derivative dengan compatibility matrix sendiri.

Hyprland upstream memperingatkan bahwa Ubuntu dapat tertinggal pada dependency dan packaged version tertentu. Oleh karena itu:

- jangan menjadikan build-from-source sebagai jalur default MVP;
- lakukan preflight versi kernel, Mesa, Wayland, GPU, display manager, portal, dan session;
- pertahankan Cinnamon sebagai recovery/fallback;
- sediakan mode server tanpa Hyprland.

Docker juga menyatakan instalasi pada Ubuntu derivative seperti Linux Mint tidak resmi didukung walaupun dapat bekerja. OMES harus mendeteksi Mint, memberi warning, dan menguji jalur paket yang dipilih; jangan menjanjikan support Docker yang setara dengan Ubuntu.

### 2.3 Hermes Agent

Hermes menyediakan installer Linux, `hermes setup`, `hermes doctor`, profile melalui `HERMES_HOME`, konfigurasi terpisah antara `config.yaml` dan secret `.env`, serta gateway messaging. Gateway mendukung user service maupun system service; server headless dapat memakai system service atau user service dengan lingering.

Implikasi desain:

- OMES tidak boleh menaruh token provider atau Telegram di repository.
- Setiap profile harus memiliki scope secret dan state yang jelas.
- `hermes config set` lebih aman daripada hand-edit YAML.
- `hermes doctor` menjadi health check wajib setelah instalasi.
- Telegram harus menggunakan numeric allowlist, bukan wildcard secara default.
- Service gateway harus mempunyai PATH eksplisit agar launcher, Node, ffmpeg, dan tooling dapat ditemukan.
- Perintah agent yang mempunyai dampak sistem harus melewati approval policy Hermes.

### 2.4 Security dan operasional

Inspirasi security Omarchy yang dapat dipakai: firewall default-deny, encrypted storage bila tersedia, update policy, SSH yang eksplisit, dan recovery path. Namun OMES tidak mengontrol bootloader atau disk encryption pada host existing; klaim security harus dibatasi pada konfigurasi yang benar-benar dikelola OMES.

Docker daemon tetap sensitif: akses grup `docker` setara root. OMES tidak boleh otomatis menambahkan user ke grup `docker` tanpa opt-in yang menjelaskan risikonya. Pilihan default:

1. `sudo docker` untuk host biasa;
2. rootless Docker bila kebutuhan dan prerequisite terpenuhi;
3. docker group hanya dengan explicit opt-in.

## 3. Arsitektur target

```text
omes/
├── README.md
├── LICENSE
├── docs/
│   ├── research-and-implementation-plan.md
│   ├── architecture.md
│   ├── ubuntu-server.md
│   ├── linux-mint.md
│   ├── hermes-integration.md
│   ├── security.md
│   ├── rollback.md
│   └── troubleshooting.md
├── install/
│   ├── bootstrap.sh
│   ├── preflight.sh
│   ├── ubuntu-server.sh
│   ├── linux-mint.sh
│   ├── desktop.sh
│   └── hermes.sh
├── modules/
│   ├── apt/
│   ├── hermes/
│   ├── services/
│   ├── desktop/
│   ├── developer-tools/
│   ├── containers/
│   └── security/
├── config/
│   ├── hypr/
│   ├── waybar/
│   ├── foot/
│   ├── shell/
│   └── nvim/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── vm/
│   └── fixtures/
└── .github/workflows/
    ├── lint.yml
    └── compatibility.yml
```

Prinsip dependency:

- `preflight` tidak melakukan mutasi;
- `install` hanya memanggil modul yang lolos preflight;
- setiap modul memiliki `check`, `apply`, `verify`, dan bila memungkinkan `rollback`;
- state OMES disimpan secara terukur, bukan berdasarkan asumsi isi file;
- root operation dipisahkan dari user operation;
- konfigurasi pengguna selalu dibackup sebelum dikelola;
- output tersedia dalam human-readable dan JSON.

## 4. Tahapan implementasi

### Phase 0 — Foundation dan keputusan desain

Issue: #1, #2, #3, #4, #5, #18, #26

Deliverables:

- scope/non-goals;
- feature inventory dan compatibility matrix;
- threat model;
- arsitektur modul;
- naming/licensing/third-party notices;
- README dan contribution rules.

Gate: tidak ada installer production sebelum OS matrix, threat model, dan rollback policy disetujui.

### Phase 1 — Preflight dan core installer

Issue: #6, #9, #10, #14

Deliverables:

- `omes check`;
- OS/version/architecture detection;
- privilege/network/disk/display/GPU checks;
- dry-run;
- structured logging;
- idempotent package installation;
- backup manifest;
- rollback/uninstall;
- stable exit codes.

Acceptance:

- fresh install dan re-run menghasilkan state sama;
- unsupported OS berhenti sebelum mutasi;
- partial failure menunjuk modul yang gagal;
- restore dapat diuji tanpa koneksi internet.

### Phase 2 — Ubuntu Server profile

Issue: #7, #12, #16, #17

Urutan:

1. base packages dan time/network checks;
2. Hermes per-user atau dedicated service user;
3. `hermes doctor`;
4. gateway service dengan systemd;
5. journald/log rotation/health command;
6. firewall/SSH policy yang explicit;
7. Docker opsional dengan rootless-first evaluation;
8. reboot and recovery test.

Acceptance:

- service hidup setelah reboot;
- credentials tidak muncul di process args atau log;
- status dan journal dapat dibaca operator;
- Telegram tidak aktif tanpa explicit setup;
- install tidak memerlukan GUI.

### Phase 3 — Linux Mint desktop profile

Issue: #8, #9, #10, #15, #17

Urutan:

1. backup Cinnamon/session config;
2. preflight GPU, kernel, Mesa, Wayland, portal, display manager;
3. install compositor/session components dari sumber yang didukung;
4. konfigurasi terminal, launcher, notification, clipboard, idle, screenshot;
5. session entry Hyprland;
6. retain Cinnamon fallback;
7. verify login, logout, suspend, multi-monitor, screen sharing, and recovery.

Acceptance:

- Cinnamon tetap dapat dipilih;
- login failure tidak membuat host unusable;
- konfigurasi dapat dikembalikan;
- unsupported GPU menghasilkan warning yang jelas.

### Phase 4 — Hermes workflow dan Telegram

Issue: #11, #12, #13, #14

Deliverables:

- installer/provider-neutral;
- profile-safe state;
- CLI diagnostics;
- gateway service;
- Telegram allowlist;
- group/topic isolation guidance;
- secret rotation and backup policy.

Acceptance:

- `hermes doctor` clean atau warning yang actionable;
- gateway status dapat dibuktikan melalui systemd;
- Telegram test hanya berhasil untuk authorized identity;
- backup tidak mengandung token.

### Phase 5 — QA dan release

Issue: #15, #16, #17, #27

Test matrix minimum:

- Ubuntu Server 24.04 amd64 VM;
- Ubuntu Server 22.04 amd64 bila dipertahankan;
- Linux Mint 22.x amd64 VM/physical test;
- fresh install;
- rerun;
- reboot;
- network unavailable;
- package failure;
- broken session rollback;
- secret scan;
- ShellCheck;
- CI smoke test.

Release gates:

- no secret in tree/history;
- no destructive default;
- documented rollback;
- success evidence on every supported profile;
- issue severity and known limitations published;
- business pilot has a measurable success threshold.

## 5. Riset bisnis dan rekomendasi

### 5.1 ICP prioritas

Urutan validasi:

1. internal ahliweb dan workstation operator;
2. freelancer/developer yang ingin setup reproducible;
3. agency web/digital dengan beberapa workstation/server;
4. small team yang membutuhkan Hermes internal;
5. self-hosters dan lab pendidikan.

Masalah yang harus divalidasi, bukan diasumsikan:

- waktu setup Linux developer terlalu lama;
- konfigurasi AI agent dan gateway sulit diulang;
- tim kecil tidak memiliki DevOps khusus;
- pengguna ingin Ubuntu/Mint stability tetapi menyukai workflow keyboard-first;
- biaya support setup lebih rendah daripada engineering time yang hilang.

### 5.2 Positioning

Positioning yang disarankan:

> OMES adalah deployment layer open-source untuk membuat Ubuntu Server dan Linux Mint siap dipakai sebagai lingkungan development dan operasi berbasis Hermes, dengan workflow desktop yang terinspirasi Omarchy namun tetap reversible dan sesuai platform host.

Jangan menggunakan klaim “Omarchy untuk Ubuntu” sebagai klaim resmi. Gunakan “Omarchy-inspired” atau “compatibility layer” dan jelaskan perbedaan teknis.

### 5.3 Model bisnis yang layak diuji

- Core open-source: installer, profile, docs, tests.
- Paid setup/migration: instalasi dan transfer workflow.
- Support subscription: update, backup, incident response, dan troubleshooting.
- Business hardening: allowlist, isolated profiles, logging, backup, policy.
- Custom integration: Telegram, GitHub, Docker, internal tools, provider routing.
- Training/workshop: Linux + AI agent operations untuk tim kecil.

Rekomendasi MVP komersial: jangan mulai dari SaaS atau hosting. Mulai dari productized service yang menggunakan OMES sebagai delivery engine; ukur waktu implementasi dan support cost sebelum menetapkan subscription.

### 5.4 Unit economics yang harus diukur

Catat per pilot:

- waktu preflight;
- waktu install;
- waktu troubleshooting;
- jumlah manual intervention;
- jumlah rollback;
- biaya compute/API/support;
- waktu onboarding Hermes;
- waktu maintenance per bulan;
- willingness-to-pay dan alasan keberatan.

Rumus dasar:

```text
gross contribution = revenue - delivery labor - infrastructure - support variable cost
payback months = acquisition/setup cost / monthly gross contribution
support burden = support hours / active deployment / month
```

Tidak ada angka pasar atau harga final yang boleh dianggap fakta sebelum customer discovery.

### 5.5 Risiko bisnis utama

- maintenance matrix Ubuntu/Mint/Hyprland terlalu mahal;
- upstream desktop berubah lebih cepat daripada kapasitas maintenance;
- support incident akibat installer salah mengubah host;
- pengguna menganggap OMES sebagai produk resmi Omarchy/Hermes;
- provider AI dan biaya token berubah;
- akses agent/Docker menciptakan insiden keamanan;
- nilai produk desktop sulit dibedakan dari dotfiles biasa.

Mitigasi:

- fokus awal pada Hermes/server reliability;
- desktop profile opt-in;
- strict compatibility matrix;
- reproducible VM tests;
- backup/rollback wajib;
- security claims konservatif;
- paid offering berbasis support dan operational outcome.

## 6. Prioritas 30/60/90 hari

### Hari 0–30

- selesaikan #1–#5;
- buat README dan architecture doc;
- implement preflight;
- implement Ubuntu Server dry-run dan check;
- validasi Hermes install/service;
- jalankan pilot internal pertama.

### Hari 31–60

- implement installer server idempotent;
- tambah rollback dan diagnostics;
- buat CI lint/secret scan;
- dokumentasikan Telegram secure profile;
- mulai Linux Mint profile pada satu hardware/VM;
- wawancara calon pengguna dan catat evidence.

### Hari 61–90

- stabilkan desktop profile;
- tambah compatibility matrix dan regression VM;
- rilis alpha;
- jalankan 2–5 pilot terkontrol;
- hitung support burden dan unit economics;
- putuskan apakah model paid setup/support layak dilanjutkan.

## 7. Kriteria go/no-go

Go ke alpha apabila:

- Ubuntu Server install, re-run, reboot, dan rollback teruji;
- Hermes service dan health check berjalan;
- secret boundary tervalidasi;
- dokumentasi dapat diikuti operator lain;
- tidak ada destructive default;
- satu pilot internal menyelesaikan workflow nyata.

Tunda desktop release apabila:

- Hyprland membutuhkan source build yang rapuh;
- Cinnamon fallback tidak terjamin;
- screen sharing/portal/GPU belum stabil;
- support matrix belum dapat dipelihara.

Tunda monetisasi apabila:

- waktu support lebih besar daripada nilai setup;
- pelanggan tidak memiliki willingness-to-pay;
- positioning tidak berbeda dari dotfiles/Ansible biasa;
- biaya maintenance lintas distro tidak dapat ditutup oleh paket layanan.

## 8. Sumber utama

1. Omarchy Manual — https://omarchy.org/manual/
2. Omarchy Getting Started — https://omarchy.org/manual/getting-started/
3. Omarchy Security — https://omarchy.org/manual/security/
4. Omarchy Updates — https://omarchy.org/manual/updates/
5. Omarchy AI — https://omarchy.org/manual/ai/
6. Omarchy upstream repository — https://github.com/omacom/omarchy
7. Hyprland Installation — https://wiki.hypr.land/Getting-Started/Installation/
8. Ubuntu Autoinstall reference — https://ubuntu.com/server/docs/install/autoinstall-reference/
9. Ubuntu Automatic Updates — https://ubuntu.com/server/docs/how-to/software/automatic-updates/
10. Linux Mint FAQ — https://linuxmint.com/faq.php
11. Docker Ubuntu installation — https://docs.docker.com/engine/install/ubuntu/
12. Docker Linux post-install security — https://docs.docker.com/install/linux/linux-postinstall
13. Hermes installation — https://hermes-agent.nousresearch.com/docs/getting-started/installation
14. Hermes messaging gateway — https://hermes-agent.nousresearch.com/docs/user-guide/messaging/
15. Hermes Telegram — https://hermes-agent.nousresearch.com/docs/user-guide/messaging/telegram
16. Hermes configuration — https://hermes-agent.nousresearch.com/docs/user-guide/configuration
17. Omakub Manual — https://manual.omakub.org/1/read

## 9. Batasan riset

- Tidak ada data pelanggan, revenue, CAC, churn, atau willingness-to-pay OMES; semua keputusan bisnis tersebut masih hipotesis.
- Compatibility claim harus dibuktikan melalui VM/hardware test, bukan hanya dokumentasi upstream.
- Versi OS, Hyprland, Docker, dan Hermes berubah; CI dan periodic review wajib menjaga freshness.
- Riset ini tidak mengklaim audit keamanan formal atau jaminan bahwa agent selalu aman.
