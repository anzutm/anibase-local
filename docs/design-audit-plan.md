# AniBase — Audit dan rencana penyempurnaan desain

Tanggal: 10 September 2026. Status: rencana, belum mengubah UI.

## Dasar audit

Audit struktur template, CSS, dan alur tautan Home, Movies, detail anime, player, Settings, Schedule, Studio, dan Seiyuu; disertai screenshot pengguna dalam percakapan. Belum merupakan pengujian visual browser terbaru. Ukuran, kontras, dan performa aktual perlu diverifikasi saat implementasi.

## Temuan

- `static/style.css` memiliki sekitar 15.500 baris dan banyak blok pemolesan berulang. Ini menambah risiko perubahan satu komponen tertimpa aturan lain; jumlah baris sendiri bukan bukti halaman lambat.
- Home, Movies, dan profil Seiyuu memakai sistem komponen berbeda. Anime dan player memiliki dokumen HTML sendiri sehingga keseragaman shell perlu dikelola secara eksplisit.
- Home menumpuk banner, poster, ambient glow, panel samping, metadata, dan label seperti HD Ready/Resume Supported. Label HD Ready tidak berasal dari pemeriksaan kualitas file pada template tersebut.
- CTA Home bertuliskan Play Now tetapi mengarah ke detail anime. Label harus sesuai tindakan: View details, atau langsung memainkan episode yang tepat.
- Settings berisi banyak kelompok dalam satu halaman panjang: backup, library, automation, player, integration, theme, maintenance. Navigasi bagian akan membantu menemukan pengaturan.
- Badge provider belum konsisten: schedule membentuk kelas CSS dari teks `Tenrai fallback`, sementara CSS warna emas menargetkan `--tenrai`; Seiyuu masih memakai gaya sendiri.
- Cache busting CSS detail/player menggunakan angka acak, sehingga URL stylesheet berubah pada setiap render. Ganti dengan versi aset yang stabil saat implementasi.
- Font utama dimuat dari Google Fonts; bundel font secara lokal agar tampilan tidak bergantung pada koneksi internet.
- Ada dukungan reduced motion dalam CSS; cakupan carousel dan animasi halaman lain masih perlu diuji.

## Arah desain: perpustakaan anime sinematik

Poster dan artwork menjadi daya tarik utama. Gunakan latar ink gelap, tiga tingkat surface, satu aksen tema untuk aksi, teks sekunder netral, dan glow hanya pada area hero atau fokus. Tema pengguna tetap didukung melalui token; warna status tidak mengikuti warna tema.

Heading 28–40 px pada desktop, judul hero maksimal sekitar 56 px, body 14–16 px, metadata 12–13 px. Gunakan skala jarak 4/8/12/16/24/32/48, radius 10 px untuk kontrol, 16 px untuk kartu, dan 24 px untuk hero. Ini target awal yang perlu divalidasi pada layout nyata.

Ikon memakai satu keluarga SVG. Hover ringan dan transisi singkat; semua aksi tetap terlihat melalui keyboard dan layar sentuh. Informasi tidak bergantung pada warna saja.

## Tahapan kerja

### 1. Baseline dan fondasi

- Ambil screenshot seluruh halaman pada 390, 768, dan 1440 px dengan data normal, kosong, judul panjang, dan metadata parsial.
- Catat waktu buka halaman serta seek lokal sebelum mengubah desain.
- Tetapkan token warna, tipografi, spacing, radius, dan komponen button/card/badge/input/empty state.
- Konsolidasikan CSS komponen yang disentuh; jangan menambah lapisan override besar atau merombak semua CSS sekaligus.
- Bundel font lokal dan pakai versi stylesheet stabil.

Selesai jika komponen dasar konsisten pada semua tema, fokus keyboard terlihat, dan halaman baseline tetap berfungsi.

### 2. Home sebagai contoh desain utama

- Hero ringkas dengan satu judul, metadata inti, dan satu aksi utama yang benar-benar sesuai labelnya.
- Prioritaskan Continue watching; kurangi tinggi hero ketika ada tontonan aktif.
- Susunan: navigasi → hero → Continue watching → library/filter. Informasi jadwal bisa ditambahkan secara ringkas jika tidak menduplikasi halaman Schedule.
- Kartu library: poster, judul, satu baris metadata, progres/status. Detail tambahan muncul lewat detail halaman, bukan tumpukan chip.
- Carousel dapat dikendalikan keyboard; hentikan autoplay saat interaksi dan hormati reduced motion.

Selesai jika tontonan terakhir dapat dilanjutkan dengan satu klik dari Home, judul panjang tidak menabrak kontrol, dan tidak ada scroll horizontal halaman pada mobile.

### 3. Detail anime dan player

- Detail: poster dan judul, aksi Resume yang dominan, metadata sekunder lebih ringkas, sinopsis dengan expand, daftar episode mudah dicari.
- Pisahkan aksi Delete dari baris Resume/Studio/Airing untuk mengurangi salah klik.
- Daftar episode menampilkan episode aktif, progres, dan status selesai tanpa badge berlebihan.
- Player: video dominan; navigasi episode dan subtitle mudah dijangkau. Status integrasi pemutar eksternal ditampilkan dekat aksi terkait.
- Ukur seek MP4/MKV dan spam +5 sebelum menentukan perbaikan buffering. Validasi fullscreen, subtitle, dan posisi feedback pada viewport video.

Selesai jika jalur Home → Resume → episode berikutnya jelas, keyboard berfungsi, dan perubahan visual tidak memperburuk pemutaran dibanding baseline.

### 4. Movies, Studio, Seiyuu, dan Schedule

- Movies mempertahankan artwork sinematik tetapi berbagi tombol, spacing, dan tipografi dengan Home.
- Studio/Seiyuu berbagi struktur profil, statistik ringkas, serta grid kredit yang konsisten.
- Gambar hilang memiliki placeholder yang sengaja didesain, tanpa ikon broken image.
- Schedule memiliki hierarki hari/jam yang lebih mudah dipindai, label timezone jelas, dan pembedaan jadwal estimasi dengan episode terkonfirmasi.
- Badge sumber memakai nilai provider terpisah dari label, termasuk unknown/local bila diperlukan. Jangan menganggap semua data sebagai AniList.

Selesai jika semua halaman terlihat satu produk dan metadata parsial tetap menghasilkan layout yang utuh.

### 5. Settings dan penyelesaian visual

- Navigasi bagian: Library, Playback, Appearance, Automation, Backup. Maintenance/integrasi tetap mudah ditemukan dalam kelompok yang relevan.
- Preview tema langsung, indikator perubahan belum disimpan, dan tombol Save yang mudah ditemukan.
- Backup menampilkan nama file terpilih dan hasil restore yang jelas; jangan mengklaim cakupan backup di luar data yang benar-benar diekspor.
- Audit loading, error, empty state, fokus modal, touch target, dan kontras seluruh tema.

Selesai jika pengaturan mudah ditemukan, penyimpanan jelas hasilnya, dan layout tetap nyaman pada mobile.

## Validasi dan batas klaim

- Jalankan tes backend yang relevan, tes interaksi browser untuk alur kritis, serta perbandingan screenshot tiap tahap.
- Target kontras teks normal 4,5:1; target sentuh sekitar 44 px; semua aksi dapat diakses keyboard.
- Uji provider gagal, gambar 404, koleksi kosong/besar, nama panjang, dan mode offline.
- Test suite sebelumnya melaporkan 172 test dijalankan dengan 1 dilewati: artinya 171 lulus, bukan 172 lulus ditambah 1 dilewati. Hasil itu belum membuktikan kualitas visual atau performa seek browser.
- Proxy gambar perlu pengujian khusus untuk redirect, ukuran unduhan, cache, dan error sebelum dianggap tuntas. Ini pekerjaan pendukung stabilitas, bukan pengganti desain.

## Urutan rekomendasi

Mulai fondasi dan redesign Home sebagai satu irisan lengkap, tinjau screenshot hasilnya, lalu terapkan bahasa visual yang sama ke detail/player dan halaman pendukung. Tidak perlu migrasi framework untuk mencapai hasil ini. Commit per tahap agar perubahan mudah ditinjau dan dipulihkan.
