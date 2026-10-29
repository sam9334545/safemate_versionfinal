package com.safemate.emergency;

import android.Manifest;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.telephony.SmsManager;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import java.util.ArrayList;

@CapacitorPlugin(
    name = "EmergencyDirect",
    permissions = {
        @Permission(
            alias = "sms",
            strings = { Manifest.permission.SEND_SMS }
        ),
        @Permission(
            alias = "call",
            strings = { Manifest.permission.CALL_PHONE }
        ),
        @Permission(
            alias = "location",
            strings = {
                Manifest.permission.ACCESS_FINE_LOCATION,
                Manifest.permission.ACCESS_COARSE_LOCATION
            }
        )
    }
)
public class EmergencyDirectPlugin extends Plugin {

    /**
     * Send direct cellular SMS using the device SIM card with ZERO user taps.
     * Uses Jio/Airtel/Vi free daily SMS quota.
     */
    @PluginMethod
    public void sendDirectSms(PluginCall call) {
        String phone = call.getString("phone");
        String message = call.getString("message");

        if (phone == null || phone.trim().isEmpty()) {
            call.reject("Recipient phone number is required");
            return;
        }
        if (message == null || message.trim().isEmpty()) {
            call.reject("Distress message text is required");
            return;
        }

        // Clean phone number (keep digits and leading +)
        String cleanPhone = phone.replaceAll("[^0-9+]", "");

        if (getPermissionState("sms") != PermissionState.GRANTED) {
            requestPermissionForAlias("sms", call, "smsPermCallback");
            return;
        }

        executeSmsSend(cleanPhone, message, call);
    }

    @PermissionCallback
    private void smsPermCallback(PluginCall call) {
        if (getPermissionState("sms") == PermissionState.GRANTED) {
            String phone = call.getString("phone");
            String message = call.getString("message");
            String cleanPhone = (phone != null) ? phone.replaceAll("[^0-9+]", "") : "";
            executeSmsSend(cleanPhone, message, call);
        } else {
            call.reject("SEND_SMS permission was denied by user");
        }
    }

    private void executeSmsSend(String cleanPhone, String message, PluginCall call) {
        try {
            SmsManager smsManager;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                smsManager = getContext().getSystemService(SmsManager.class);
            } else {
                smsManager = SmsManager.getDefault();
            }

            if (smsManager == null) {
                smsManager = SmsManager.getDefault();
            }

            ArrayList<String> parts = smsManager.divideMessage(message);
            if (parts.size() > 1) {
                smsManager.sendMultipartTextMessage(cleanPhone, null, parts, null, null);
            } else {
                smsManager.sendTextMessage(cleanPhone, null, message, null, null);
            }

            JSObject result = new JSObject();
            result.put("success", true);
            result.put("status", "SENT_CELLULAR_SIM");
            result.put("phone", cleanPhone);
            result.put("partsCount", parts.size());
            call.resolve(result);
        } catch (Exception ex) {
            call.reject("Failed to dispatch cellular SMS: " + ex.getMessage(), ex);
        }
    }

    /**
     * Immediately dial an emergency number (e.g. 112) with zero taps.
     */
    @PluginMethod
    public void makeDirectCall(PluginCall call) {
        String number = call.getString("number", "112");
        if (number == null || number.trim().isEmpty()) {
            number = "112";
        }
        String cleanNumber = number.replaceAll("[^0-9+]", "");

        if (getPermissionState("call") != PermissionState.GRANTED) {
            requestPermissionForAlias("call", call, "callPermCallback");
            return;
        }

        executeCall(cleanNumber, call);
    }

    @PermissionCallback
    private void callPermCallback(PluginCall call) {
        if (getPermissionState("call") == PermissionState.GRANTED) {
            String number = call.getString("number", "112");
            String cleanNumber = (number != null) ? number.replaceAll("[^0-9+]", "") : "112";
            executeCall(cleanNumber, call);
        } else {
            // If direct call permission is not granted, fallback to system dialer
            fallbackToDialer(call.getString("number", "112"), call);
        }
    }

    private void executeCall(String cleanNumber, PluginCall call) {
        try {
            Intent callIntent = new Intent(Intent.ACTION_CALL);
            callIntent.setData(Uri.parse("tel:" + cleanNumber));
            callIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(callIntent);

            JSObject result = new JSObject();
            result.put("success", true);
            result.put("status", "CALL_PLACED");
            result.put("number", cleanNumber);
            call.resolve(result);
        } catch (SecurityException secEx) {
            // Some Android versions restrict direct ACTION_CALL for emergency numbers like 112 to prevent pocket dials
            fallbackToDialer(cleanNumber, call);
        } catch (Exception ex) {
            fallbackToDialer(cleanNumber, call);
        }
    }

    private void fallbackToDialer(String cleanNumber, PluginCall call) {
        try {
            Intent dialIntent = new Intent(Intent.ACTION_DIAL);
            dialIntent.setData(Uri.parse("tel:" + cleanNumber));
            dialIntent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(dialIntent);

            JSObject result = new JSObject();
            result.put("success", true);
            result.put("status", "DIALER_PROMPTED");
            result.put("number", cleanNumber);
            call.resolve(result);
        } catch (Exception ex) {
            call.reject("Could not launch phone dialer: " + ex.getMessage(), ex);
        }
    }

    /**
     * Proactively request SMS and Telephony permissions upon app launch.
     */
    @PluginMethod
    public void requestAllEmergencyPermissions(PluginCall call) {
        requestPermissions(call);
    }
}

