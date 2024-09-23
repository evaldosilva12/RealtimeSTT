<?php
// Get the raw POST data
$data = file_get_contents("php://input");

// Decode the JSON data
$formData = json_decode($data, true);

// Define the file path where setup data will be stored
$file = 'setup_data.json';

// Save the form data as JSON
if (file_put_contents($file, json_encode($formData, JSON_PRETTY_PRINT))) {
    // Run the batch file to start the server
    // exec('start "" "C:\\wamp64\\www\\RealtimeSTT\\example_browserclient4\\start_server.bat"');

    // Return a success response in JSON format
    echo json_encode(['status' => 'success', 'message' => 'Data saved and server started']);
} else {
    // Return an error response in JSON format
    echo json_encode(['status' => 'error', 'message' => 'Error saving data']);
}
?>
