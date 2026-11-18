# Langkah berikutnya untuk USD Scalper

## Kondisi sekarang

Dashboard berjalan di mesin lokal dengan FastAPI dan SQLite. Lo bisa melihat harga referensi dan meninjau keputusan M3, membandingkan laporan backtest, serta mencatat fill aktual Exness. Ringkasan menggabungkan laporan mingguan, kesehatan feed, pengiriman Telegram, dan backup SQLite. [Panduan menjalankan dashboard](README.md#jalankan-dashboard)

## Prioritas berikutnya

| Urutan | Pekerjaan | Kriteria selesai |
| --- | --- | --- |
| 1 | Uji strategi dasar pada beberapa bulan tick Exness, dengan periode pengembangan dan periode evaluasi yang terpisah. | Simpan rentang tanggal, biaya, konfigurasi, dan laporan per simbol. Bandingkan transaksi, hasil dalam R, dan drawdown dengan baseline XAUUSD −7,21 R dan EURUSD −31,73 R pada 2–8 Januari 2025 UTC. |
| 2 | Tambahkan perbandingan hasil historis dan fill aktual per kelompok jam. | Bandingkan jumlah trade, rata-rata R, dan asumsi biaya pada rentang yang sama. Sajikan ukuran sampel per simbol dan kelompok jam. |
| 3 | Tambahkan histori agenda FOMC, ECB, dan rilis lain sesuai instrumen. | Setiap event menyimpan waktu event dan waktu pertama kali diketahui. Backtest berulang pada periode yang sama bisa membandingkan strategi teknikal dan strategi dengan filter konteks. |
| 4 | Sambungkan alert TradingView melalui HTTPS dan uji di lingkungan demo. | Satu candle M3 menghasilkan satu keputusan. Uji satu pengiriman ulang alert, status feed, dan Telegram opsional. Latih pencatatan trade tanpa mengandalkan akun riil. |
| 5 | Siapkan server untuk penggunaan rutin. | Tentukan apakah perlu mulai otomatis setelah reboot. Jalankan uji pemulihan ke file baru dengan data aktual dan siapkan cara melihat kegagalan feed atau backup dari luar laptop jika dibutuhkan. |

## Keputusan yang masih terbuka

- Tentukan instrumen dan jam trading yang akan dipakai untuk evaluasi berikutnya. Fokus awal proyek adalah XAUUSD dan EURUSD.
- Tentukan format input risiko per transaksi dan batas rugi harian jika nanti ingin menambah pencatatan risiko akun. Jurnal saat ini menghitung R bruto dari fill dan stop awal yang dicatat.
- Tentukan apakah webhook perlu berjalan saat laptop mati. Aplikasi yang hanya berjalan di `127.0.0.1` bisa dibuka di laptop yang sama. Alert live memerlukan alamat HTTPS yang bisa dijangkau TradingView. [Panduan webhook proyek](README.md#hubungkan-tradingview)
