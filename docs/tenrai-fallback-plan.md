# Rencana fallback Tenrai untuk AniBase

Status: fallback Tenrai terintegrasi pada metadata, jadwal, studio, seiyuu,
pencarian, UI provider, cache, dan pemulihan AniList.
Tanggal uji: 10 September 2026.

## Keputusan

Tenrai layak digunakan sebagai fallback metadata dengan AniList tetap utama.
Integrasi memerlukan adapter, pengelolaan identitas, dan perubahan cache;
bukan sekadar mengganti URL GraphQL dengan URL REST.

Dokumentasi resmi:
- https://api.tenrai.org/documentation
- https://api.tenrai.org/documentation/openapi.json
- Base URL: https://api.tenrai.org/v1

## Temuan kode saat ini

- `main.py:get_anilist_info`: mengambil metadata, karakter, relasi, rekomendasi.
- `get_cached_anilist_info`: cache JSON per judul, validasi kelengkapan, download gambar.
  Cache yang lengkap belum memiliki kebijakan TTL umum; cache Tenrai dapat bertahan
  selamanya jika langsung memakai aturan ini.
- `search_anilist_anime`, `/api/anilist/search`, dan endpoint pencocokan metadata:
  hasil dan pilihan manual terikat `anilist_id`.
- `save_anilist_mapping`: menyimpan provider AniList, AniList ID, MAL ID bila
  tersedia, judul, dan waktu perubahan. Query metadata kini meminta `idMal`.
- Jadwal, katalog studio, dan profil seiyuu mempunyai request AniList tersendiri.
- Profil seiyuu memakai rantai AniList -> Tenrai; fallback Jikan sudah tidak
  dipanggil oleh halaman.
- `templates/anime.html`, `base.html`, `studio.html`, `seiyuu.html` memiliki
  label/tautan yang mengasumsikan AniList. ID pengisi suara dipakai pada route AniList.

## Bukti uji langsung

Jalankan ulang: `python tools/probe_tenrai.py`.
Script menggunakan GET publik tanpa API key dan tidak mengimpor `main.py`.
Tidak mengubah library, settings, atau cache metadata pengguna.
Hasil mentah sesi ini ada di `temp/tenrai-probe.json` (file sementara).

| Request | HTTP | Durasi | Hasil |
| --- | --- | --- | --- |
| `/anime?q=Sousou%20no%20Frieren&limit=3` | 200 | 1.48 s | 3 kandidat |
| `/anime/52991/full` | 200 | 0.38 s | Frieren, 28 episode, metadata dan relasi |
| `/anime/52991/characters` | 200 | 0.48 s | 63 karakter, termasuk pengisi suara Jepang |
| `/anime/52991/recommendations` | 200 | 0.92 s | 81 rekomendasi |
| `/schedules?filter=thursday&limit=2` | 200 | 0.45 s | 2 entri jadwal |
| `/producers?q=Madhouse&limit=2` | 200 | 0.52 s | 2 kandidat produser |
| `/anime?producers=11&limit=2` | 200 | 0.39 s | 2 judul katalog |
| `/people/126/full` | 200 | 0.45 s | Profil dan voices |

Semua respons mempunyai `data` nonkosong. Ini membuktikan akses dan bentuk data
sampel, bukan jaminan uptime, semua judul, atau keberhasilan integrasi UI.
Script keluar dengan kode 1 jika request gagal atau data kosong.
Dokumentasi saat diperiksa menyebut batas publik 4 request/detik,
120/menit, dan 40.000/hari; implementasi wajib menghormati HTTP 429/Retry-After.

## Pemetaan data

| Data AniBase | Data Tenrai / perlakuan |
| --- | --- |
| title | `title_english` lalu `title` |
| description | `synopsis`; tetap sanitasi sesuai pemakaian template |
| episodes, year | `episodes`, `year`, pertahankan null |
| duration | Parse durasi teks ke menit; dukung jam dan nilai unknown |
| format, season | Normalisasi enum; misalnya Movie -> MOVIE, fall -> FALL |
| status | Finished Airing -> FINISHED; Currently Airing -> RELEASING; Not yet aired -> NOT_YET_RELEASED |
| score | Skala 0–10 ke 0–100; null tidak menjadi 0 |
| genres, studio | Nama dari objek `genres` dan `studios` |
| poster | `images.jpg.large_image_url` dengan fallback ukuran lain |
| characters | Endpoint characters; MAIN lebih dahulu, maksimal 6; voice actor Japanese |
| recommendations | Endpoint recommendations; urut votes, maksimal 10 |
| relations | Relasi anime dari full; sampel tidak menyertakan poster/status relasi, gunakan placeholder atau enrichment terbatas |
| banner | Tidak ada padanan dalam sampel; gunakan banner cache judul yang sama atau placeholder yang ada |
| next_airing | Tidak ada padanan episode/timestamp dalam sampel; null, sembunyikan countdown |
| schedule | `broadcast` memberikan hari/jam/zona, bukan kepastian episode berikutnya; tampilkan jadwal mingguan dengan keterangan yang sesuai |

## Tahap implementasi

1. **Identitas dan kontrak provider.** Tambahkan `idMal` pada query AniList;
   simpan `mal_id`, `source`, `fetched_at`, dan versi schema pada metadata.
   Pisahkan ID anime/staff/studio berdasarkan provider; jangan masukkan MAL ID
   ke `anilist_id` atau `va_staff_id`. Cache lama tanpa source dibaca sebagai
   AniList secara kompatibel. Simpan MAL ID pada mapping ketika tersedia.

2. **Client Tenrai dan adapter.** Buat modul terpisah dengan session, timeout,
   validasi JSON/schema, limiter thread-safe, dan penanganan 429/Retry-After.
   Metadata inti tetap berhasil jika enrichment karakter/rekomendasi gagal.
   Jangan unduh detail setiap relasi tanpa batas; pakai cache/placeholder.

3. **Pemilihan judul.** Utamakan MAL ID tersimpan. Untuk judul tanpa mapping,
   bandingkan judul utama/alias, season, tahun, dan format jika tersedia.
   Jangan otomatis memilih kandidat pertama yang ambigu. Mapping manual AniList
   tanpa MAL ID harus mempertahankan cache judul yang dipilih; jika crosswalk
   belum bisa dibuktikan, minta pemilihan Tenrai eksplisit melalui UI.
   Pemilihan manual menyimpan provider + ID dengan kompatibilitas mapping lama.

4. **Urutan fallback dan pemulihan.** Sajikan cache valid terlebih dahulu.
   Saat perlu jaringan, coba AniList; pada timeout/koneksi gagal/429/5xx atau
   respons rusak gunakan Tenrai. Bedakan no-result dan kesalahan query dari outage;
   pencarian no-result boleh mencoba Tenrai tanpa menandai AniList sedang down.
   Saat keduanya gagal, gunakan cache lama yang identitasnya cocok.
   Usulan awal: cooldown AniList 60 detik setelah kegagalan layanan, meningkat
   sampai 5 menit; hormati Retry-After. Satu request probe saat cooldown berakhir.
   Cache Tenrai punya jadwal percobaan kembali ke AniList setelah cooldown,
   melalui refresh background agar UI tidak menunggu timeout berulang.
   Setelah AniList pulih, tulis metadata AniList secara atomik. Jangan menimpa
   banner/metadata AniList yang masih berguna dengan nilai kosong dari Tenrai.

5. **Integrasi bertahap.** Pertama halaman anime, poster, pencarian, dan manual
   match. Berikutnya karakter/seiyuu, studio, dan jadwal. Tambahkan route yang
   mengenali provider untuk seiyuu dan tautan studio; filter katalog producer
   berdasarkan keanggotaan `studios`, karena producer tidak selalu studio animasi.
   Jadwal Tenrai tidak boleh dipaksa menjadi episode countdown AniList.
   Perbarui label sumber sesuai data aktual dan placeholder untuk field kosong.

6. **Verifikasi.** Tambahkan unit/integration test terisolasi dengan request mock
   dan temporary cache/settings. Jalankan tes regresi repository menggunakan
   environment pengembangan proyek. Lakukan smoke test UI setelah adapter masuk.
   Periksa build jika modul baru memerlukan perubahan bundling.

## Kriteria lulus integrasi

- AniList sehat: metadata berasal dari AniList dan Tenrai tidak dipanggil.
- Simulasi timeout, 429, 503, HTML non-JSON, atau GraphQL service error:
  metadata Tenrai tersedia tanpa halaman crash; error query tidak disamarkan.
- AniList pulih: cache fallback akhirnya kembali memakai AniList.
- Dua provider gagal: cache cocok tetap dipakai; tanpa cache tampil keadaan kosong.
- Pencocokan manual dan season tidak berubah menjadi judul lain secara diam-diam.
- MAL staff/studio ID tidak menghasilkan tautan AniList yang salah.
- Data parsial/null dan enrichment gagal tidak menggagalkan metadata inti.
- Skala skor, durasi, status, gambar, relasi, dan label sumber tampil benar.
- Tidak ada countdown episode palsu dari jadwal mingguan.
- Request bersamaan mematuhi limiter/cooldown dan tidak menyebabkan lonjakan retry.
- Tes regresi watch history, manual mapping, dan fitur yang tersentuh tetap lulus.

## Status pekerjaan

Sudah selesai: identitas provider, adapter Tenrai, fallback metadata/jadwal/
studio/seiyuu dengan cooldown/limiter/cache, pemulihan ke AniList, label UI,
simulasi failover, dan tes regresi repository.

Masih tersisa: smoke test UI manual setelah deploy dan pemantauan kualitas
pencocokan katalog producer Tenrai pada studio yang memiliki nama ambigu.
