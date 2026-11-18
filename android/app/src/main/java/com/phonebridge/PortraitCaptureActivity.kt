package com.phonebridge

import com.journeyapps.barcodescanner.CaptureActivity
import com.journeyapps.barcodescanner.DecoratedBarcodeView

/** Leitor de QR em pé, com moldura quadrada (o padrão da biblioteca abre deitado e retangular). */
class PortraitCaptureActivity : CaptureActivity() {
    override fun initializeContent(): DecoratedBarcodeView {
        setContentView(R.layout.activity_qr_scan)
        return findViewById(R.id.zxing_barcode_scanner)
    }
}
