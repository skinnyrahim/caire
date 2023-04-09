<?php

if ($_SERVER['REQUEST_METHOD'] == 'POST') {
    $message = $_POST['message'];
    $output = shell_exec('python chatbot.py "' . $message . '"');
    echo $output;
}

?>
