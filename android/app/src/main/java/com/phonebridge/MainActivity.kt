package com.phonebridge

import android.Manifest
import android.app.Activity
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.TextView

class MainActivity : Activity() {

    private lateinit var ipField: EditText
    private lateinit var portField: EditText
    private lateinit var modeGroup: RadioGroup
    private lateinit var button: Button
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = getSharedPreferences("pb", Context.MODE_PRIVATE)
        val pad = (16 * resources.displayMetrics.density).toInt()

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad * 2, pad, pad)
        }

        root.addView(TextView(this).apply {
            text = "PhoneBridge"
            textSize = 26f
        })

        ipField = EditText(this).apply {
            hint = "IP do PC (ex.: 192.168.0.10)"
            setText(prefs.getString("ip", ""))
            inputType = InputType.TYPE_CLASS_TEXT
        }
        root.addView(ipField)

        portField = EditText(this).apply {
            hint = "Porta"
            setText(prefs.getString("port", "5800"))
            inputType = InputType.TYPE_CLASS_NUMBER
        }
        root.addView(portField)

        modeGroup = RadioGroup(this).apply {
            orientation = RadioGroup.VERTICAL
            addView(RadioButton(this@MainActivity).apply {
                text = "Usar o celular como MICROFONE do PC"
                id = 1
            })
            addView(RadioButton(this@MainActivity).apply {
                text = "Usar o celular como ALTO-FALANTE do PC"
                id = 2
            })
            check(prefs.getInt("mode", 1))
        }
        root.addView(modeGroup)

        button = Button(this).apply { setOnClickListener { toggle() } }
        root.addView(button)

        status = TextView(this).apply {
            gravity = Gravity.START
            setPadding(0, pad, 0, 0)
        }
        root.addView(status)

        setContentView(root)
        AudioService.listener = { msg, running ->
            runOnUiThread {
                status.text = msg
                button.text = if (running) "Parar" else "Conectar"
            }
        }
        refresh()
    }

    private fun refresh() {
        button.text = if (AudioService.running) "Parar" else "Conectar"
        status.text = AudioService.lastStatus
    }

    private fun toggle() {
        if (AudioService.running) {
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
            status.text = "Permissão de microfone negada."
        }
    }

    private fun start() {
        val ip = ipField.text.toString().trim()
        val port = portField.text.toString().toIntOrNull() ?: 5800
        val mode = if (modeGroup.checkedRadioButtonId == 2) 2 else 1
        if (ip.isEmpty()) {
            status.text = "Informe o IP do PC."
            return
        }
        getSharedPreferences("pb", Context.MODE_PRIVATE).edit()
            .putString("ip", ip).putString("port", port.toString()).putInt("mode", mode).apply()
        val i = Intent(this, AudioService::class.java)
            .setAction(AudioService.ACTION_START)
            .putExtra("ip", ip).putExtra("port", port).putExtra("mode", mode)
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(i) else startService(i)
    }

    override fun onDestroy() {
        AudioService.listener = null
        super.onDestroy()
    }
}
