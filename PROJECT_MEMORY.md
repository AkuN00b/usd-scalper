# Memori proyek USD Scalper

## Pilihan yang sudah disepakati

- Fokus awal XAUUSD dan EURUSD di Exness. Watchlist juga mencakup GBPUSD, USDJPY, USDCHF, USDCAD, AUDUSD, dan NZDUSD.
- Scalping memakai candle M15 yang sudah selesai untuk arah dan M3 untuk keputusan.
- Output utama BUY, SELL, atau WAIT dengan alasan. Eksekusi akun Exness manual. Berita atau sinyal baru tidak menutup posisi yang sudah dibuka.
- Backtest perlu menyimpan waktu sinyal, entry, dan exit. Utamakan tick Exness bila ada, lalu CSV candle MT5 sebagai alternatif.
- Gunakan sumber data gratis lebih dulu. TradingView webhook dan Telegram Bot API opsional bergantung konfigurasi. Repo bisa dijalankan dengan GitHub Actions.
- Lot, risiko per trade, dan batas rugi harian belum ditentukan oleh pengguna.

## Kondisi sekarang

- Strategi awal menggunakan EMA M15/M3, breakout, RSI, dan ATR. Skor minimal 85. SL 1,2 ATR, target 1,5 kali jarak SL, dan batas simulasi 30 menit.
- Kalender BLS dan feed Fed, ECB, UN News menjadi sumber dasar. GDELT opsional sempat merespons 503 saat uji. Berita berdampak tinggi merupakan filter entry, tanpa klasifikasi arah oleh model AI.
- Backtest teknikal dengan tick Exness 2–8 Januari 2025 UTC memakai delay satu detik, komisi nol, dan slippage 0,02 XAUUSD atau 0,00001 EURUSD. XAUUSD mencatat 123 trade dengan −7,21 R. EURUSD mencatat 129 trade dengan −31,73 R. Keduanya rugi pada sampel ini. [Ringkasan dan laporan](README.md#hasil-uji-yang-disertakan)
- Jurnal lokal mencatat fill, lot, SL/TP awal, P/L broker, biaya, mata uang, dan catatan. Tautan ke sinyal menambahkan selisih harga fill jika referensinya tersedia. Filter status, sisi, dan tanggal entry UTC berlaku pada ringkasan dan CSV. P/L bersih dijumlahkan per mata uang.
- Dashboard menyimpan tema, aksen, tampilan ringkas, instrumen aktif, dan watchlist per browser. Watchlist menampilkan keputusan terakhir dan status feed wajib. Grafik pantauan memakai harga referensi sinyal. Tab Backtest membandingkan dua laporan dengan grafik R kumulatif, profit factor, dan rata-rata R per transaksi.

## Usulan urutan berikutnya

Gunakan [NEXT_STEPS.md](NEXT_STEPS.md) untuk prioritas dan kriteria selesai tiap pekerjaan.

1. Bandingkan strategi awal dengan varian dalam beberapa periode terpisah, beserta asumsi spread, slippage, jam trading, dan drawdown. Simpan setiap versi agar hasilnya bisa ditelusuri.
2. Tambahkan tape replay per sesi dan ringkasan mingguan dari jurnal untuk membandingkan harga sinyal dengan fill aktual.
3. Perluas kalender FOMC, ECB, dan event relevan lain, lalu bandingkan efek filter berita hanya ketika waktu ketersediaan berita historis bisa dibuktikan.
4. Tambahkan ringkasan Telegram harian dan filter spread berdasarkan data broker yang tersedia.

Perbarui file ini ketika ada keputusan baru atau hasil uji baru. Di chat lain, berikan file ini bersama CSV jurnal atau laporan yang ingin dibahas. Simpan kredensial di `.env` dan catatan transaksi pribadi di SQLite atau hasil ekspor yang lo pilih untuk bagikan.
