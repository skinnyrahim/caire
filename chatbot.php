<?php

if ($_SERVER['REQUEST_METHOD'] == 'POST') {
    $message = $_POST['message'];
    $output = shell_exec('python app.py "' . $message . '"');
    echo $output;
}

?>
