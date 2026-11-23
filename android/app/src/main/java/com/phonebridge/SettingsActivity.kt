package com.phonebridge

import android.os.Bundle
import android.text.InputType
import androidx.appcompat.app.AppCompatActivity
import androidx.preference.EditTextPreference
import androidx.preference.PreferenceFragmentCompat
import com.google.android.material.dialog.MaterialAlertDialogBuilder

class SettingsActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        if (savedInstanceState == null) {
            supportFragmentManager.beginTransaction()
                .replace(R.id.settingsContainer, SettingsFragment()).commit()
        }
    }

    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }

    class SettingsFragment : PreferenceFragmentCompat() {
        override fun onCreatePreferences(savedInstanceState: Bundle?, rootKey: String?) {
            setPreferencesFromResource(R.xml.preferences, rootKey)
            findPreference<EditTextPreference>("port")?.setOnBindEditTextListener {
                it.inputType = InputType.TYPE_CLASS_NUMBER
            }
            findPreference<androidx.preference.Preference>("paired")?.setOnPreferenceClickListener {
                showPaired()
                true
            }
            findPreference<androidx.preference.Preference>("battery")?.setOnPreferenceClickListener {
                activity?.let { a -> MainActivity.requestIgnoreBattery(a) }
                true
            }
            findPreference<androidx.preference.ListPreference>("theme")?.setOnPreferenceChangeListener { _, v ->
                MainActivity.applyTheme(v as String)
                true
            }
        }

        /** Lista os PCs pareados; tocar em um pergunta se quer remover o pareamento. */
        private fun showPaired() {
            val ctx = context ?: return
            val store = PairingStore(ctx)
            val list = store.all()
            if (list.isEmpty()) {
                MaterialAlertDialogBuilder(ctx).setTitle(R.string.pref_paired).setMessage(R.string.no_paired)
                    .setPositiveButton(android.R.string.ok, null).show()
                return
            }
            MaterialAlertDialogBuilder(ctx)
                .setTitle(R.string.pref_paired)
                .setItems(list.map { "${it.name} (${it.host})" }.toTypedArray()) { _, i ->
                    val pc = list[i]
                    MaterialAlertDialogBuilder(ctx)
                        .setMessage(getString(R.string.forget_confirm, pc.name))
                        .setPositiveButton(R.string.forget) { _, _ -> store.remove(pc.pcid) }
                        .setNegativeButton(android.R.string.cancel, null)
                        .show()
                }
                .setPositiveButton(android.R.string.ok, null)
                .show()
        }
    }
}
