<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Setup Page</title>
    <style>
        /* General reset for clean styling */
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Arial', sans-serif;
            background-color: #f7f9fc;
            color: #333;
            display: flex;
            justify-content: center;
            align-items: center;
            height: 100vh;
        }

        h1 {
            text-align: center;
            font-size: 2rem;
            color: #444;
            margin-bottom: 20px;
        }

        #setupForm {
            background-color: #fff;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 4px 12px rgba(0, 0, 0, 0.1);
            width: 100%;
            max-width: 500px;
        }

        label {
            display: block;
            margin-bottom: 8px;
            font-weight: bold;
            color: #555;
        }

        textarea {
            width: 100%;
            padding: 10px;
            border: 1px solid #ddd;
            border-radius: 4px;
            font-size: 1rem;
            margin-bottom: 20px;
            resize: none;
            height: 100px;
        }

        button {
            width: 100%;
            padding: 12px;
            background-color: #007bff;
            border: none;
            border-radius: 4px;
            color: #fff;
            font-size: 1rem;
            cursor: pointer;
            transition: background-color 0.3s ease;
        }

        button:hover {
            background-color: #0056b3;
        }

        #message {
            margin-top: 20px;
            text-align: center;
            font-size: 1rem;
        }

        p {
            margin-bottom: 0;
        }

        @media (max-width: 600px) {
            body {
                padding: 20px;
            }

            #setupForm {
                padding: 15px;
            }
        }
    </style>
</head>
<body>
    <div id="setupForm">
        <h1>Setup</h1>
        <form id="setupForm">
            <label for="systemRole">System Role:</label>
            <textarea id="systemRole" name="systemRole" placeholder="Enter system role..."></textarea>
            
            <label for="additionalInfo">Additional Info:</label>
            <textarea id="additionalInfo" name="additionalInfo" placeholder="Enter additional info..."></textarea>
            
            <button type="button" onclick="saveSetup()">Save</button>
        </form>

        <!-- Message display area -->
        <div id="message"></div>
    </div>

    <script>
        function saveSetup() {
            var systemRole = document.getElementById('systemRole').value;
            var additionalInfo = document.getElementById('additionalInfo').value;

            var formData = {
                system_role: systemRole,
                additional_info: additionalInfo
            };

            var xhr = new XMLHttpRequest();
            xhr.open("POST", "save_setup.php", true);
            xhr.setRequestHeader("Content-Type", "application/json;charset=UTF-8");
            xhr.onreadystatechange = function () {
                if (xhr.readyState === 4) {
                    document.getElementById('message').innerHTML = '';
                    if (xhr.status === 200) {
                        try {
                            var response = JSON.parse(xhr.responseText);
                            if (response.status === 'success') {
                                document.getElementById('message').innerHTML = '<p style="color:green;">Setup saved and server started successfully!</p>';
                            } else {
                                document.getElementById('message').innerHTML = '<p style="color:red;">Error: ' + response.message + '</p>';
                            }
                        } catch (e) {
                            document.getElementById('message').innerHTML = '<p style="color:red;">Error: Invalid server response.</p>';
                        }
                    } else {
                        document.getElementById('message').innerHTML = '<p style="color:red;">Error: Failed to save setup.</p>';
                    }
                }
            };
            xhr.send(JSON.stringify(formData));
        }
    </script>
</body>
</html>
