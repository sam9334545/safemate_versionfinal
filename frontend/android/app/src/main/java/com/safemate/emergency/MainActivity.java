package com.safemate.emergency;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(EmergencyDirectPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
