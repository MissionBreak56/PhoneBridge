package com.phonebridge

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.Menu
import android.view.MenuItem
import android.view.WindowManager
import android.widget.SeekBar
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AppCompatDelegate
import androidx.preference.PreferenceManager
import com.google.android.material.button.MaterialButton
import com.google.android.material.button.MaterialButtonToggleGroup
import com.google.android.material.materialswitch.MaterialSwitch
import com.google.android.material.textfield.TextInputEditText
import android.widget.TextView

class MainActivity : AppCompatActivity() {

    companion object {
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

        ipField.setText(prefs.getString("ip", ""))
        modeGroup.check(if (prefs.getInt("mode", 1) == 2) R.id.modeSpeaker else R.id.modeMic)
        levelSeek.progress = prefs.getInt("level", 4)
        AudioService.level = levelSeek.progress
        updateLevelText()

        mainButton.setOnClickListener { toggle() }
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
        mainButton.text = getString(if (state == AudioService.STATE_STOPPED) R.string.start else R.string.stop)
        modeGroup.isEnabled = state == AudioService.STATE_STOPPED
        findViewById<android.view.View>(R.id.modeMic).isEnabled = state == AudioService.STATE_STOPPED
        findViewById<android.view.View>(R.id.modeSpeaker).isEnabled = state == AudioService.STATE_STOPPED
        ipField.isEnabled = state == AudioService.STATE_STOPPED
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
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            needed += Manifest.permission.RECORD_AUDIO
        }
        if (Build.VERSION.SDK_INT >= 33 &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            needed += Manifest.permission.POST_NOTIFICATIONS
        }
        if (needed.isNotEmpty()) {
            requestPermissions(needed.toTypedArray(), 10)
            return
        }
        start()
    }

    override fun onRequestPermissionsResult(code: Int, perms: Array<out String>, results: IntArray) {
        super.onRequestPermissionsResult(code, perms, results)
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
            start()
        } else {
            AudioService.setError(getString(R.string.perm_denied))
        }
    }

    private fun start() {
        val ip = ipField.text.toString().trim()
        if (ip.isEmpty()) {
            AudioService.setError(getString(R.string.enter_ip))
            return
        }
        val port = prefs.getString("port", "5800")?.toIntOrNull() ?: 5800
        val mode = currentMode()
        val source = prefs.getString("audioSource", "0")?.toIntOrNull() ?: 0
        prefs.edit().putString("ip", ip).putInt("mode", mode).apply()
        val i = Intent(this, AudioService::class.java)
            .setAction(AudioService.ACTION_START)
            .putExtra("ip", ip).putExtra("port", port).putExtra("mode", mode)
            .putExtra("source", source)
            .putExtra("autoMute", prefs.getBoolean("autoMute", false))
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i) else startService(i)
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
