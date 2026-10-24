# OMES Control Center dan Manajemen AI Hermes — Kontrak UI/UX v1.0

**24 Oktober 2026 — dokumen desain tambahan, bukan implementasi baru.** Issue [#348](https://github.com/ahliweb/omes/issues/348). Menggunakan sistem desain **yang sudah diterima** pada [ADR-0023](adr/0023-control-center-ui-ux-design-system.md), [UI/UX Design System](ui-ux-design-system.md), [integrasi Control Center](control-center-and-integrations.md), dan [batas runtime Bun/AWCMS](awcms-bun-channel-realtime-boundary.md). Web admin SYSTEM dibangun di AWCMS Astro, OMES memiliki runtime host/kontrak, Hermes Agent menjalankan model/sesi/skill dan penalaran. Tidak boleh membangun framework agen baru.

## 1. Layar yang sudah ada dan masih direncanakan

| ID | Layar | Status | Pemilik |
| --- | --- | --- | --- |
| OM-01 | Fleet overview /admin/omes | SUDAH ADA | OMES #200/#201 |
| OM-02 | Server/drift | SUDAH ADA | AWCMS OMES Control |
| OM-03 | Deployments | SUDAH ADA | AWCMS OMES Control |
| OM-04 | Jobs dan Operations | SUDAH ADA dengan typed allowlist | OMES Control |
| OM-05 | Health/Doctor | SUDAH ADA | OMES Control |
| OM-06 | Backup/restore | SUDAH ADA sebatas kontrak izin | OMES Control |
| OM-07 | Audit log | SUDAH ADA | OMES Control |
| OM-08 | Worker enrollment | SUDAH ADA | OMES Control |
| OM-09 | Architecture /mission-control | Route sudah ada, tetapi snapshot/read-only, bukan kontrol tanpa batas | OMES Control |
| OM-10 | Workspace manajemen agen AI, approval berizin | RENCANA | OMES #343/#344 |
| OM-11 | Digital twin organisasi 3D dan coordinator | RENCANA | #263/#264/#277 |
| OM-12 | Browser-transfer, checkpoint dan intervensi operator | RENCANA | #294/#300–#306 |
| OM-13 | SSE/WS Bun untuk proyeksi progres CRM | RENCANA, menunggu ADR | AWCMS #1168/#1171 |
| OM-14 | Diagnostik runtime/dependency/versi yang didukung | RENCANA | OMES #347 |

## 2. Pertahankan token desain OMES

Sistem desain OMES yang sudah ditetapkan: latar belakang **#0E1216**, card **#151A20**, sidebar **#11161B**, border **#242C35**, teks **#E6EDF3**, cyan primary **#5FC8D6**, hijau verified healthy **#6FD08C**, amber drift **#E8B44A**, merah error **#E86D5B**, violet control-plane **#8B9CF7**. Gunakan tipografi padat, monospace pada hash/ID/version/correlation, kontras dan teks status, bukan warna saja.

Navigasi: Fleet → Servers & Drift → Deployments → Operations & Jobs → Hermes Orchestration (read-only awal) → Health → Backups → Audit → Enrollment. Command palette hanya menawarkan operasi yang memang memiliki kontrak bertipe dan hak akses. Operator dapat mengusulkan, approver berizin menyetujui; pemilihan role di UI **bukan** jaminan hak server.

## 3. Detail layar dan alur

**OM-01 Fleet:** kartu total host, sehat, stale, gagal operasi, backup terakhir; tabel disortir risiko dengan hostname tersamarkan sesuai izin, OS, role, heartbeat terakhir, drift, tekanan CPU/memori. Klik membuka detail dengan cek server. Heartbeat lama tidak boleh mengubah status menjadi hijau.

**OM-02 Server/Drift:** detail OS/kernel/arch, systemd/container, versi/deployment, desired vs observed, perbandingan konfigurasi dan provenance. Remediation hanya melalui request typed job, dengan preview risiko dan rollback. Tidak tersedia shell SSH bebas dari UI.

**OM-03 Deployments:** daftar target host, digest image, versi diinginkan/terpasang, health, waktu, bukti deployment dan kesiapan rollback. Deploy ulang melalui proposal/approval, bukan teks perintah bebas. Gagal harus bisa diperiksa berdasarkan correlation.

**OM-04 Jobs/Operations:** tab Queued/Planning/Approval Required/Applying/Succeeded/Failed/Unknown; filter host/actor/tanggal dan detail typed plan, idempotency key, approval, audit serta progres. Alur operator request→validasi→owner approve→OMES host HTTPS pull bertanda tangan→hasil→reconciliation. Cancel hanya sebelum pengiriman bila kontrak mengizinkan; operasi OS sedang berjalan tidak boleh diklaim dapat dibatalkan.

**OM-05 Health:** pemeriksaan host, layanan, runtime agen, model/provider dan AWCMS. Setiap status mempunyai sumber/otoritas, cap waktu, rentang ambang, selisih dan saran perbaikan aman. Endpoint model yang hidup bukan bukti semua skill siap. Tidak ada remote shell di browser.

**OM-06 Backups:** tampilkan backup diminta vs berhasil diverifikasi, checksum, retensi, pemulihan yang diuji dan recovery point. Restore perlu peringatan risiko/downtime, target, dry-run, approval, bukti rollback. Jangan tampilkan lokasi secret.

**OM-07 Audit:** siapa mengusulkan/menyetujui/menolak/mengirim, target, correlation dan bukti tanpa token/PII; filter dan pagination, ekspor dibatasi role/tenant. Immutable event tetap otoritatif.

**OM-08 Enrollment:** wizard enrollment dengan token singkat, keypair lokal bertanda tangan dan koneksi **HTTPS outbound-only** dari OMES host. UI status pending/enrolled/offline/revoked/expired tanpa menampilkan key. Tidak ada tombol membuka port WS/gRPC/SSH privileged ke host.

**OM-09 Architecture/Mission Control:** tampilan arsitektur adalah pinned release snapshot, tree/graf memuat provenance, timestamp, label read-only/stale. Fitur digital twin 3D #263 masih rencana. Sediakan tampilan 2D keyboard-accessible dan reduced-motion.

**OM-10 AI Management (rencana):** tab Agents/Sessions/Policies/Knowledge Egress/Approvals/Budgets/Audit. Kartu ID agen, provider/model, izin kemampuan, scope, health, waktu observasi, estimasi biaya jika tersedia, kesalahan dan tugas terhubung. Proposal menjelaskan tujuan, typed tool allowlist, klasifikasi data, budget, checkpoint manusia dan kedaluwarsa. Approve/Deny berdasarkan policy server; tidak boleh membaca database privat Hermes, menjalankan root shell atau mengekspor credential. Mode CRM Assist default, bukan autonomous send.

**OM-11 Digital Twin (rencana):** graf coordinator/worker/tugas yang memproyeksikan fakta AWCMS/OMES/Hermes, bukan mesin agen kedua. Klik node membawa operator ke rute detail 2D existing. Harus ada alternatif non-3D, keyboard, screen reader, reduced motion dan label data usang.

**OM-12 Browser Transfer (rencana):** wizard Sumber/Tujuan→Mapping→Privasi & Approval→Dry-run→Login/MFA manusia→Submit durable job→Progress checkpoint per-record→Read-back tujuan→Hasil final rekonsiliasi. Status menunggu/MFA/running/uncertain write/verified/gagal/dibatalkan; retry tidak boleh menulis ganda. Eksekusi melalui adapter terotorisasi, bukan perintah browser arbitrary dari OMES.

**OM-13 Bun realtime (rencana):** AWCMS server dapat menyediakan SSE Bun untuk progres satu arah; WS native Bun hanya jika ADR #1168 membuktikan kebutuhan dua arah. **OMES host tetap Python/Bash/systemd**, HTTP signed pull keluar tanpa listener privileged inbound. Origin/role/tiket dan redaksi status wajib, frame tidak membawa arbitrary host command/prompt/secret.

**OM-14 Dependency/versions (rencana):** tabel installed/latest available/latest supported/held back/risk/last verified pada host, agent, tool. Bun v1.4.4 relevan untuk AWCMS atau tooling Bun in-repo, **tidak memaksa Python host menjadi Bun**. Python, OS, systemd dan Hermes mengikuti matriks kompatibilitas Ubuntu 22.04/24.04/26.04 serta Mint 22. Pembaruan menjadi PR atomic dan rollback, tanpa tombol “upgrade all hosts” yang menjalankan shell tanpa kontrol.

## 4. State, aksesibilitas, keamanan

Setiap layar: Loading/Empty/Permission Denied/Stale/Unknown/Offline/Error/Approval Required/Running/Verified Success; tampilkan sumber dan waktu aktual. Aksi destruktif punya konfirmasi serta persetujuan, tidak diputuskan oleh role switch klien. Retry idempotent. Keyboard, fokus modal, mobile 360px, WCAG 2.2 AA, graf 2D, ekspor tersanitasi. Tidak boleh memuat data lintas-tenant atau token.

## 5. Dependensi dan syarat rilis

UI/UX CRM penuh [AWCMS-One #472](https://github.com/ahliweb/awcms-one/issues/472), upstream [AWCMS #1174](https://github.com/ahliweb/awcms/issues/1174), Astro publik [#127](https://github.com/ahliweb/awcms-astro/issues/127), OMES AI #343/#344, Mission Control #263/#264/#277, browser transfer #294/#300–#306. Batas host [ADR-0027](adr/0027-control-center-pull-worker-transport.md), runtime [#345](https://github.com/ahliweb/omes/issues/345), versi/kompatibilitas [#347](https://github.com/ahliweb/omes/issues/347).

Acceptance: rute AWCMS dibandingkan kontrak nyata, tidak ada tombol palsu, tidak ada runtime Hermes ganda, listener privileged inbound tidak dibuat, negative test tenant/approval/audit, mobile/WCAG, status stale dan pemulihan. Jalankan gate dokumentasi/security/matriks host sesuai repo sebelum merge. **Tidak ada perubahan runtime dalam PR dokumen ini.**
