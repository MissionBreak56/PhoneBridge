package com.phonebridge

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.widget.Toast
import android.view.Menu
import android.view.MenuItem
import android.view.WindowManager
import android.widget.SeekBar
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.preference.PreferenceManager
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import com.google.android.material.materialswitch.MaterialSwitch
import com.google.android.material.textfield.TextInputEditText
import android.widget.TextView

class MainActivity : AppCompatActivity() {

    companion object {
        private const val REQ_PERMISSIONS = 10

        /** Abre a tela do Android que permite ignorar a otimização de bateria deste app. */
        fun requestIgnoreBattery(a: android.app.Activity) {
            try {
                a.startActivity(
                    Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${a.packageName}"))
                )
            } catch (_: Exception) {
                try { a.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) } catch (_: Exception) {}
            }
        }

        fun applyTheme(v: String) {
            AppCompatDelegate.setDefaultNightMode(
                when (v) {
                    "1" -> AppCompatDelegate.MODE_NIGHT_NO
                    "2" -> AppCompatDelegate.MODE_NIGHT_YES
                    else -> AppCompatDelegate.MODE_NIGHT_FOLLOW_SYSTEM
                }
            )
        }
    }

    private lateinit var stateText: TextView
    private lateinit var mainButton: MaterialButton
    private lateinit var modeGroup: MaterialButtonToggleGroup
    private lateinit var ipField: TextInputEditText
    private lateinit var muteSwitch: MaterialSwitch
    private lateinit var levelText: TextView
    private lateinit var levelSeek: SeekBar
    private lateinit var scanButton: MaterialButton
    private lateinit var qrButton: MaterialButton
    private var scanning = false
    private lateinit var pairings: PairingStore

    private val scanLauncher = registerForActivityResult(ScanContract()) { result ->
        val text = result.contents ?: return@registerForActivityResult
        handleQr(text)
    }

    private val prefs by lazy { PreferenceManager.getDefaultSharedPreferences(this) }

    override fun onCreate(savedInstanceState: Bundle?) {
        applyTheme(prefs.getString("theme", "0") ?: "0")
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        stateText = findViewById(R.id.stateText)
        mainButton = findViewById(R.id.mainButton)
        modeGroup = findViewById(R.id.modeGroup)
        ipField = findViewById(R.id.ipField)
        muteSwitch = findViewById(R.id.muteSwitch)
        levelText = findViewById(R.id.levelText)
        levelSeek = findViewById(R.id.levelSeek)
        pairings = PairingStore(this)
        scanButton = findViewById(R.id.scanButton)
        qrButton = findViewById(R.id.qrButton)

        ipField.setText(prefs.getString("ip", ""))
        modeGroup.check(if (prefs.getInt("mode", 1) == 2) R.id.modeSpeaker else R.id.modeMic)
        levelSeek.progress = prefs.getInt("level", 4)
        AudioService.level = levelSeek.progress
        updateLevelText()

        mainButton.setOnClickListener { toggle() }
        scanButton.setOnClickListener { searchPcs(silent = false) }
        qrButton.setOnClickListener { scanQr() }
        modeGroup.addOnButtonCheckedListener { _, _, _ -> updateLevelText() }
        muteSwitch.setOnCheckedChangeListener { _, checked -> AudioService.muted = checked }
        levelSeek.setOnSeekBarChangeListener(object : SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(sb: SeekBar, p: Int, fromUser: Boolean) {
                AudioService.level = p
                prefs.edit().putInt("level", p).apply()
                updateLevelText()
            }
            override fun onStartTrackingTouch(sb: SeekBar) {}
            override fun onStopTrackingTouch(sb: SeekBar) {}
        })
        muteSwitch.isChecked = AudioService.muted

        // primeira vez (ainda sem IP): procura sozinho
        if (savedInstanceState == null && ipField.text.isNullOrBlank()) searchPcs(silent = true)
    }

    override fun onStart() {
        super.onStart()
        AudioService.listener = { render() }
        render()
    }

    override fun onStop() {
        AudioService.listener = null
        super.onStop()
    }

    private fun currentMode() = if (modeGroup.checkedButtonId == R.id.modeSpeaker) 2 else 1

    private fun updateLevelText() {
        val res = if (currentMode() == 1) R.string.gain else R.string.volume
        levelText.text = getString(res, levelSeek.progress)
    }

    private fun render() = runOnUiThread {
        val state = AudioService.state
        stateText.text = when (state) {
            AudioService.STATE_CONNECTED -> getString(R.string.state_connected)
            AudioService.STATE_STARTING -> getString(R.string.state_starting)
            else -> AudioService.errorMessage ?: getString(R.string.state_stopped)
        }
        if (state == AudioService.STATE_STARTING && AudioService.info != null) stateText.text = AudioService.info
        mainButton.text = getString(if (state == AudioService.STATE_STOPPED) R.string.start else R.string.stop)
        modeGroup.isEnabled = state == AudioService.STATE_STOPPED
        findViewById<android.view.View>(R.id.modeMic).isEnabled = state == AudioService.STATE_STOPPED
        findViewById<android.view.View>(R.id.modeSpeaker).isEnabled = state == AudioService.STATE_STOPPED
        ipField.isEnabled = state == AudioService.STATE_STOPPED
        qrButton.isEnabled = state == AudioService.STATE_STOPPED
        scanButton.isEnabled = state == AudioService.STATE_STOPPED && !scanning
        if (state != AudioService.STATE_STOPPED && prefs.getBoolean("screenOn", false)) {
            window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        } else {
            window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        }
    }

    private fun toggle() {
        if (AudioService.state != AudioService.STATE_STOPPED) {
            startService(Intent(this, AudioService::class.java).setAction(AudioService.ACTION_STOP))
            return
        }
        val needed = mutableListOf<String>()
        // o microfone só é necessário no modo microfone; no modo alto-falante o celular só toca o som do PC
        if (currentMode() == 1 &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED
        ) {
            needed += Manifest.permission.RECORD_AUDIO
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED &&
            !prefs.getBoolean("notifAsked", false)
        ) {
            needed += Manifest.permission.POST_NOTIFICATIONS
            prefs.edit().putBoolean("notifAsked", true).apply()
        }
        if (needed.isNotEmpty()) {
            requestPermissions(needed.toTypedArray(), REQ_PERMISSIONS)
            return
        }
        start()
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(code, perms, results)
        if (code != REQ_PERMISSIONS) return
        // negar as notificações não impede o áudio (o serviço continua em primeiro plano); negar o microfone impede o modo microfone
        val micDenied = currentMode() == 1 &&
            checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED
        if (micDenied) AudioService.setError(getString(R.string.perm_denied)) else start()
    }

    private fun start() {
        val ip = ipField.text.toString().trim()
        if (ip.isEmpty()) {
            AudioService.setError(getString(R.string.enter_ip))
            return
        }
        var port = prefs.getString("port", "5800")?.toIntOrNull() ?: 5800
        val paired = pairings.findByHost(ip)
        val allowLegacy = prefs.getBoolean("allowLegacy", false)
        if (paired == null && !allowLegacy) {
            AudioService.setError(getString(R.string.not_paired))
            return
        }
        if (paired != null) port = paired.port
        val mode = currentMode()
        val source = prefs.getString("audioSource", "0")?.toIntOrNull() ?: 0
        prefs.edit().putString("ip", ip).putInt("mode", mode).apply()
        val i = Intent(this, AudioService::class.java)
            .setAction(AudioService.ACTION_START)
            .putExtra("ip", ip).putExtra("port", port).putExtra("mode", mode)
            .putExtra("source", source)
            .putExtra("opus", prefs.getString("codec", "0") == "1")
            .putExtra("reconnect", prefs.getBoolean("autoReconnect", true))
            .putExtra("aec", prefs.getBoolean("aec", false))
            .putExtra("ns", prefs.getBoolean("ns", false))
            .putExtra("autoMute", prefs.getBoolean("autoMute", false))
            .putExtra("pcid", paired?.pcid)
            .putExtra("allowLegacy", allowLegacy)
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i) else startService(i)
        askBatteryOnce()
    }

    /** Pergunta uma vez se pode ignorar a otimização de bateria (evita o Android matar o áudio). */
    private fun askBatteryOnce() {
        if (prefs.getBoolean("batteryAsked", false)) return
        val pm = getSystemService(POWER_SERVICE) as PowerManager
        if (pm.isIgnoringBatteryOptimizations(packageName)) return
        prefs.edit().putBoolean("batteryAsked", true).apply()
        MaterialAlertDialogBuilder(this)
            .setTitle(R.string.battery_title)
            .setMessage(R.string.battery_message)
            .setPositiveButton(R.string.allow) { _, _ -> requestIgnoreBattery(this) }
            .setNegativeButton(R.string.not_now, null)
            .show()
    }

    // ---- descoberta automática e QR code
    private fun applyHost(ip: String, port: Int) {
        ipField.setText(ip)
        prefs.edit().putString("ip", ip).putString("port", port.toString()).apply()
    }

    private fun searchPcs(silent: Boolean) {
        if (scanning || AudioService.state != AudioService.STATE_STOPPED) return
        scanning = true
        scanButton.isEnabled = false
        scanButton.setText(R.string.searching)
        Thread {
            val found = try { DiscoveryClient.scan() } catch (_: Exception) { emptyList() }
            runOnUiThread { onScanResult(found, silent) }
        }.start()
    }

    private fun onScanResult(found: List<DiscoveryClient.Host>, silent: Boolean) {
        scanning = false
        scanButton.setText(R.string.find_pc)
        scanButton.isEnabled = AudioService.state == AudioService.STATE_STOPPED
        if (found.isEmpty()) {
            if (!silent) Toast.makeText(this, R.string.no_pc_found, Toast.LENGTH_LONG).show()
            return
        }
        // se o PC é um dos pareados, atualiza o endereço dele (o IP pode ter mudado)
        fun use(h: DiscoveryClient.Host) {
            if (h.pcid.isNotEmpty()) pairings.touch(h.pcid, h.ip, h.port)
            applyHost(h.ip, h.port)
            if (pairings.findByHost(h.ip) == null && !silent) {
                Toast.makeText(this, R.string.found_not_paired, Toast.LENGTH_LONG).show()
            }
        }
        if (found.size == 1) {
            use(found[0])
        } else {
            val labels = found.map {
                val tag = if (it.pcid.isNotEmpty() && pairings.findByPcid(it.pcid) != null) "" else " " + getString(R.string.not_paired_tag)
                "${it.name} (${it.ip})$tag"
            }
            MaterialAlertDialogBuilder(this)
                .setTitle(R.string.choose_pc)
                .setItems(labels.toTypedArray()) { _, i -> use(found[i]) }
                .show()
        }
    }

    private fun scanQr() {
        scanLauncher.launch(
            ScanOptions()
                .setDesiredBarcodeFormats(ScanOptions.QR_CODE)
                .setPrompt(getString(R.string.qr_prompt))
                .setBeepEnabled(false)
                .setCaptureActivity(PortraitCaptureActivity::class.java)
                .setOrientationLocked(true)
        )
    }

    /** QR de pareamento: phonebridge://IP:PORTA?v=3&id=...&pid=...&k=...&n=... (veja PROTOCOL.md). */
    private fun handleQr(text: String) {
        val link = PairingLink.parse(text)
        if (link == null) {
            val legacyQr = text.trim().startsWith("phonebridge://")
            Toast.makeText(this, if (legacyQr) R.string.qr_old else R.string.qr_invalid, Toast.LENGTH_LONG).show()
            return
        }
        pairings.save(PairedPc(link.pcid, link.name, link.host, link.port, link.pid, link.secret, System.currentTimeMillis()))
        applyHost(link.host, link.port)
        Toast.makeText(this, getString(R.string.pairing_saved, link.name), Toast.LENGTH_LONG).show()
        if (AudioService.state == AudioService.STATE_STOPPED) toggle() // um toque só: lê o QR e já conecta
    }

    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.main_menu, menu)
        return true
    }

    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        if (item.itemId == R.id.action_settings) {
            startActivity(Intent(this, SettingsActivity::class.java))
            return true
        }
        return super.onOptionsItemSelected(item)
    }
}
