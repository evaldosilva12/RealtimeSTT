# from PIL import ImageGrab

# def take_screenshot():
#     path = 'screenshot.jpg'
#     screenshot = ImageGrab.grab()
#     rgb_screenshot = screenshot.convert('RGB')
#     rgb_screenshot.save(path, quality=15)

# take_screenshot()


from PIL import ImageGrab

def take_screenshot():
    path = 'screenshot.jpg'
    
    # Define the bounding box for the region you want to capture
    # Example: (left, top, right, bottom)
    bbox = (0, 140, 2200, 1820)  # Adjust these coordinates as needed
    
    screenshot = ImageGrab.grab(bbox=bbox)
    rgb_screenshot = screenshot.convert('RGB')
    rgb_screenshot.save(path, quality=15)

take_screenshot()
