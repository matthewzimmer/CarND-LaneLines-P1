# importing some useful packages
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import numpy as np
import cv2
import math
import colorsys
import os

# Import everything needed to edit/save/watch video clips
from moviepy.editor import VideoFileClip

#   Some OpenCV functions (beyond those introduced in the lesson) that might be useful for this project are:
#
#   cv2.inRange()         for color selection
#
#       http://docs.opencv.org/2.4/modules/core/doc/operations_on_arrays.html?highlight=inrange#cv2.inRange
#
#
#   cv2.fillPoly()        for regions selection
#
#       http://docs.opencv.org/2.4/modules/core/doc/drawing_functions.html?highlight=fillpoly#cv2.fillPoly
#
#
#   cv2.line()            to draw lines on an image given endpoints
#
#       http://docs.opencv.org/2.4/modules/core/doc/drawing_functions.html?highlight=line#cv2.line
#
#
#   cv2.addWeighted()     to coadd / overlay two images cv2.cvtColor() to grayscale or change color cv2.imwrite() to
#                           output images to file
#
#       http://docs.opencv.org/2.4/modules/core/doc/operations_on_arrays.html?highlight=addweighted#cv2.addWeighted
#
#
#   cv2.bitwise_and()     to apply a mask to an image
#
#       http://docs.opencv.org/2.4/modules/core/doc/operations_on_arrays.html?highlight=bitwise_and#cv2.bitwise_and
#
#
#   Check out the OpenCV documentation to learn about these and discover even more awesome functionality!
#
#
#   Below are some helper functions to help get you started. They should look familiar from the lesson!
#
#   from helpers import FUNCTION_NAME


# This constant ultimately contributes to deriving a given
# period when computing SMA and EMA for line noise smoothing
FPS = 30


class LaneLine:
    def __init__(self, x1, y1, x2, y2):
        self.x1 = x1
        self.y1 = y1
        self.x2 = x2
        self.y2 = y2

    def angle(self):
        return math.atan2(self.y2 - self.y1, self.x2 - self.x1) * 180.0 / np.pi

    def slope(self):
        return (self.y2 - self.y1) / (self.x2 - self.x1)

    def y_intercept(self):
        return self.y1 - self.slope() * self.x1

    def __str__(self):
        return "(x1, y1, x2, y2, slope, y_intercept, angle) == (%s, %s, %s, %s, %s, %s, %s)" % (
            self.x1, self.y1, self.x2, self.y2, self.slope(), self.y_intercept(), self.angle())


class HoughTransformPipeline:
    def __init__(self, rho=1, theta=np.pi / 180, threshold=1, min_line_length=10, max_line_gap=1):
        self.rho = rho
        self.theta = theta
        self.threshold = threshold
        self.min_line_length = min_line_length
        self.max_line_gap = max_line_gap


class PipelineContext:
    def __init__(self,
                 colorspace=None,
                 thickness=5,
                 gaussian_kernel_size=5,
                 canny_low_threshold=50,
                 canny_high_threshold=150,
                 region_bottom_offset=55,
                 region_vertice_weights=np.array([(1, 1), (0.48, 0.60), (0.54, 0.60), (1, 1)]),
                 hough_transform_pipeline=HoughTransformPipeline(),
                 line_color=[255, 0, 0],
                 ema_period_alpha=0.65):
        self.thickness = thickness
        self.gaussian_kernel_size = gaussian_kernel_size  # Must be an odd number (3, 5, 7...)
        self.canny_low_threshold = canny_low_threshold
        self.canny_high_threshold = canny_high_threshold
        self.region_bottom_offset = region_bottom_offset
        self.region_vertice_weights = region_vertice_weights
        self.hough_transform_pipeline = hough_transform_pipeline
        self.line_color = line_color
        self.vertices = None
        self.colorspace = colorspace
        self.current_frame = 0

        self.l_abs_min_y = None
        self.r_abs_min_y = None

        self.l_m_measurements = np.array([])
        self.l_b_measurements = np.array([])
        self.l_m_ema = 0
        self.l_b_ema = 0

        self.r_m_measurements = np.array([])
        self.r_b_measurements = np.array([])
        self.r_m_ema = 0
        self.r_b_ema = 0

        self.ema_fps_period = ema_period_alpha * FPS

    def process_video(self, src_video_path, dst_video_path, audio=False):
        self.current_frame = 0
        VideoFileClip(src_video_path).fl_image(self.process_image).write_videofile(dst_video_path, audio=audio)

    def process_image(self, image):
        self.current_frame += 1

        cvt_img = image
        if self.colorspace is 'yuv':
            cvt_img = self.yuv(image)
            gray_img = cvt_img[:, :, 0]

        elif self.colorspace == 'hls':
            cvt_img = self.hls(image)
            gray_img = cvt_img[:, :, 1]

        elif self.colorspace == 'hsv':
            cvt_img = self.hsv(image)
            gray_img = cvt_img[:, :, 2]
        else:
            # call as plt.imshow(gray, cmap='gray') to show a grayscaled image
            gray_img = self.grayscale(cvt_img)

        # Define a kernel size for Gaussian smoothing / blurring
        blur_img = self.gaussian_noise(gray_img, self.gaussian_kernel_size)

        # Define our parameters for Canny and run it
        low_threshold = self.canny_low_threshold
        high_threshold = self.canny_high_threshold
        edges = self.canny(blur_img, low_threshold, high_threshold)

        # if self.current_frame > 0:
        #     mpimg.imsave('{}_orig'.format(str(self.current_frame)), image)
        #     mpimg.imsave("{}_orig_gray".format(str(self.current_frame)), self.grayscale(image), cmap='gray')
        #     mpimg.imsave("{}_{}_gray".format(str(self.current_frame), self.colorspace), gray_img, cmap='gray')

        # This time we are defining a four sided polygon to mask
        imshape = image.shape

        bottom_offset = self.region_bottom_offset
        img_height = imshape[0]
        img_width = imshape[1]

        # (W, H) == (x, y)
        self.vertices = np.array([
            [
                # bottom left
                (bottom_offset, img_height) * self.region_vertice_weights[0],

                # top left
                (img_width, img_height) * self.region_vertice_weights[1],

                # top right
                (img_width, img_height) * self.region_vertice_weights[2],

                # bottom right
                (img_width - bottom_offset, img_height) * self.region_vertice_weights[3]
            ]
        ], dtype=np.int32)

        masked_edges = self.region_of_interest(edges)

        # Define the Hough transform parameters
        # Make a blank the same size as our image to draw on

        hough = self.hough_lines(image, masked_edges)

        α = 0.8
        β = 0.6
        λ = 0.
        weighted_hough = self.weighted_img(hough, image, α, β, λ)

        return weighted_hough

    @staticmethod
    def hls(img):
        """Converts colorspace from RGB to HLS
        This will return an image with HLS color space
        but NOTE: to see the returned image as HLS
        you should call plt.imshow(hls)"""
        return cv2.cvtColor(img, cv2.COLOR_BGR2HLS)

    @staticmethod
    def hsv(img):
        """Converts colorspace from RGB to HSV
        This will return an image with HSV color space
        but NOTE: to see the returned image as HSV
        you should call plt.imshow(hsv)"""
        return cv2.cvtColor(img, cv2.COLOR_BGR2HSV)

    @staticmethod
    def yuv(img):
        """Converts colorspace from RGB to YUV
        This will return an image with YUV color space
        but NOTE: to see the returned image as YUV
        you should call plt.imshow(yuv)"""
        return cv2.cvtColor(img, cv2.COLOR_BGR2YUV)

    @staticmethod
    def grayscale(img):
        """Applies the Grayscale transform
        This will return an image with only one color channel
        but NOTE: to see the returned image as grayscale
        you should call plt.imshow(gray, cmap='gray')"""
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def canny(img, low_threshold, high_threshold):
        """Applies the Canny transform"""
        return cv2.Canny(img, low_threshold, high_threshold)

    @staticmethod
    def gaussian_noise(img, kernel_size):
        """Applies a Gaussian Noise kernel"""
        return cv2.GaussianBlur(img, (kernel_size, kernel_size), 0)

    def region_of_interest(self, img):
        """
        Applies an image mask.

        Only keeps the region of the image defined by the polygon
        formed from `vertices`. The rest of the image is set to black.
        """
        # defining a blank mask to start with
        mask = np.zeros_like(img)

        # defining a 3 channel or 1 channel color to fill the mask with depending on the input image
        if len(img.shape) > 2:
            channel_count = img.shape[2]  # i.e. 3 or 4 depending on your image
            ignore_mask_color = (255,) * channel_count
        else:
            ignore_mask_color = 255

        # filling pixels inside the polygon defined by "vertices" with the fill color
        cv2.fillPoly(mask, self.vertices, ignore_mask_color)

        # returning the image only where mask pixels are nonzero
        masked_image = cv2.bitwise_and(img, mask)
        return masked_image

    def compute_ema(self, measurement, all_measurements, curr_ema):
        sma = sum(all_measurements) / (len(all_measurements))

        if len(all_measurements) < self.ema_fps_period:
            # let's just use SMA until
            # our EMA buffer is filled
            return sma

        multiplier = 2 / float(len(all_measurements) + 1)
        ema = (measurement - curr_ema) * multiplier + curr_ema

        # print("sma: %s, multiplier: %s" % (sma, multiplier))
        return ema

    @staticmethod
    def compute_least_squares_line(lines):
        all_x1 = []
        all_y1 = []
        all_x2 = []
        all_y2 = []

        for line in lines:
            x1, y1, x2, y2, angle, m, b = line.x1, line.y1, line.x2, line.y2, line.angle(), line.slope(), line.y_intercept()
            all_x1.append(x1)
            all_y1.append(y1)
            all_x2.append(x2)
            all_y2.append(y2)

        all_x = (all_x1 + all_x2)
        all_y = (all_y1 + all_y2)

        # This is a tab less precise
        # mean_x = sum(all_x) / len(all_x)
        # mean_y = sum(all_y) / len(all_y)

        # m = sum([(xi - mean_x) * (yi - mean_y) for xi, yi in zip(all_x, all_y)]) / sum([(xi - mean_x) ** 2 for xi in zip(all_x)])
        # b = mean_y - m * mean_x
        # print('m: %s, b: %s' % (m, b))
        # return m[0], b[0]

        n = len(all_x)

        all_x_y_dot_prod = sum([xi * yi for xi, yi in zip(all_x, all_y)])
        all_x_squares = sum([xi ** 2 for xi in all_x])

        a = ((n * all_x_y_dot_prod) - (sum(all_x) * sum(all_y))) / ((n * all_x_squares) - (sum(all_x) ** 2))
        b = ((sum(all_y) * all_x_squares) - (sum(all_x) * all_x_y_dot_prod)) / ((n * all_x_squares) - (sum(all_x) ** 2))

        # print('m: %s, b: %s' % (m, b))

        return a, b

    def draw_left_line(self, img, lines):
        # y value for bottom left vertice...this is the
        # principle y1 used during extrapolation
        abs_max_y = self.vertices[0][0][1]

        all_y2 = []
        for line in lines:
            all_y2.append(line.y2)

        # Least squares is a wee bit smoother than simply averaging slopes and intercepts
        m, b = self.compute_least_squares_line(lines)

        # Computes the EMA of all measurements over time for an even more smooth/stable line
        # See self.ema_period_alpha to adjust the number of elements in a given period
        # to track.
        self.l_m_measurements = np.append(self.l_m_measurements, m)
        self.l_b_measurements = np.append(self.l_b_measurements, b)

        self.l_m_ema = self.compute_ema(m, self.l_m_measurements, self.l_m_ema)
        self.l_b_ema = self.compute_ema(b, self.l_b_measurements, self.l_b_ema)

        if len(self.l_m_measurements) > self.ema_fps_period:
            self.l_m_measurements = np.delete(self.l_m_measurements, 0)
        if len(self.l_b_measurements) > self.ema_fps_period:
            self.l_b_measurements = np.delete(self.l_b_measurements, 0)

        # print("m=%s, b=%s, l_m_ema=%s, l_b_ema=%s" % (m, b, self.l_m_ema, self.l_b_ema))

        m = self.l_m_ema
        b = self.l_b_ema

        # Smooth out our y2 by remembering the smallest y2.
        # doesn't work well on curves at which point I would switch to a
        # different algorithm for curve analysis

        # extrapolate
        if self.l_abs_min_y is None:
            self.l_abs_min_y = min(all_y2)
        y2 = min(self.l_abs_min_y, int(sum(all_y2) / len(all_y2)))
        self.l_abs_min_y = y2

        y1 = abs_max_y
        x1 = int((y1 - b) / m)
        x2 = int((y2 - b) / m)

        cv2.line(img, (x1, y1), (x2, y2), self.line_color, self.thickness)

    def draw_right_line(self, img, lines):
        # y value for bottom right vertice
        abs_max_y = self.vertices[0][3][1]

        all_y1 = []
        for line in lines:
            all_y1.append(line.y1)

        # Least squares is a wee bit smoother than simply averaging slopes and intercepts
        m, b = self.compute_least_squares_line(lines)

        # Computes the EMA of all measurements over time for an even more smooth/stable line
        # See self.ema_period_alpha to adjust the number of elements in a given period
        # to track.
        self.r_m_measurements = np.append(self.r_m_measurements, m)
        self.r_b_measurements = np.append(self.r_b_measurements, b)

        self.r_m_ema = self.compute_ema(m, self.r_m_measurements, self.r_m_ema)
        self.r_b_ema = self.compute_ema(b, self.r_b_measurements, self.r_b_ema)

        if len(self.r_m_measurements) > self.ema_fps_period:
            self.r_m_measurements = np.delete(self.r_m_measurements, 0)
        if len(self.r_b_measurements) > self.ema_fps_period:
            self.r_b_measurements = np.delete(self.r_b_measurements, 0)

        # print("m=%s, b=%s, r_m_ema=%s, r_b_ema=%s" % (m, b, self.r_m_ema, self.r_b_ema))

        m = self.r_m_ema
        b = self.r_b_ema

        # Smooth out our y1 by remembering the smallest y1
        # doesn't work well on curves at which point I would switch to a
        # different algorithm for curve analysis

        # extrapolate
        if self.r_abs_min_y is None:
            self.r_abs_min_y = min(all_y1)
        y1 = min(self.r_abs_min_y, int(sum(all_y1) / len(all_y1)))
        self.r_abs_min_y = y1

        x1 = int((self.r_abs_min_y - b) / m)
        y2 = abs_max_y
        x2 = int((y2 - b) / m)

        cv2.line(img, (x1, y1), (x2, y2), self.line_color, self.thickness)

    def draw_lines(self, img, lines):
        """
        NOTE: this is the function you might want to use as a starting point once you want to
        average/extrapolate the line segments you detect to map out the full
        extent of the lane (going from the result shown in raw-lines-example.mp4
        to that shown in P1_example.mp4).

        Think about things like separating line segments by their
        slope ((y2-y1)/(x2-x1)) to decide which segments are part of the left
        line vs. the right line.  Then, you can average the position of each of
        the lines and extrapolate to the top and bottom of the lane.

        This function draws `lines` with `color` and `thickness`.
        Lines are drawn on the image inplace (mutates the image).
        If you want to make the lines semi-transparent, think about combining
        this function with the weighted_img() function below
        """

        if lines is None or len(lines) <= 0:
            print('ERROR: frame ', self.current_frame, ' has no lines detected.')
            return

        left_lines = []
        right_lines = []

        # This iteration splits each line into their respective line side bucket.
        # Negative line angles are left lane lines
        # Positive line angles are right lane lines
        # We also filter out outlier lines such as horizontal lines by specifying a
        # range of acceptable angles. There is likely a better way but I feel
        # this is accurate enough for first pass.
        for line in lines:
            for x1, y1, x2, y2 in line:
                # An offset may be specified to compensate for pixels that are made up by
                # erroneous data such as a hood or dashboard reflection

                # compute the angle of the line - it's just easier for me to visualize in
                # degrees than float ranges
                angle = math.atan2(y2 - y1, x2 - x1) * 180.0 / np.pi

                if angle is not 0.:
                    lane_line = LaneLine(x1, y1, x2, y2)

                    # left lane line
                    if -50 < angle <= -25:
                        left_lines.append(lane_line)

                    # right lane line
                    elif 20 <= angle <= 45:
                        right_lines.append(lane_line)

                        # else:
                        #     print('OOB line detected in frame ', self.current_frame, ': ', line_tuple)

        if len(left_lines) > 0:
            self.draw_left_line(img, left_lines)
        else:
            print('ERROR: frame ', self.current_frame, ' has no LEFT lines detected.')

        if len(right_lines) > 0:
            self.draw_right_line(img, right_lines)
        else:
            print('ERROR: frame ', self.current_frame, ' has no RIGHT lines detected.')

    def hough_lines(self, orig_img, img):
        """
        `img` should be the output of a Canny transform.

        Returns an image with hough lines drawn.
        """
        lines = cv2.HoughLinesP(img, self.hough_transform_pipeline.rho, self.hough_transform_pipeline.theta,
                                self.hough_transform_pipeline.threshold, np.array([]),
                                minLineLength=self.hough_transform_pipeline.min_line_length,
                                maxLineGap=self.hough_transform_pipeline.max_line_gap)
        # line_img = np.zeros(img.shape, dtype=np.uint8)
        line_img = np.copy(orig_img) * 0  # creating a blank to draw lines on

        self.draw_lines(line_img, lines)
        return line_img

    @staticmethod
    def weighted_img(img, initial_img, α=0.8, β=1., λ=0.):
        """
        `img` is the output of the hough_lines(), An image with lines drawn on it.
        Should be a blank image (all black) with lines drawn on it.

        `initial_img` should be the image before any processing.

        The result image is computed as follows:

        initial_img * α + img * β + λ
        NOTE: initial_img and img must be the same shape!
        """
        return cv2.addWeighted(initial_img, α, img, β, λ)


# This pipeline context is sufficient for all test_images as well as for solidWhiteRight.mp4
pipeline_context = PipelineContext(gaussian_kernel_size=3, canny_low_threshold=50, canny_high_threshold=150,
                                   region_bottom_offset=55,
                                   region_vertice_weights=np.array([(1, 1), (0.48, 0.60), (0.54, 0.60), (1, 1)]),
                                   hough_transform_pipeline=HoughTransformPipeline(rho=2, theta=np.pi / 180,
                                                                                   threshold=20,
                                                                                   min_line_length=50,
                                                                                   max_line_gap=200),
                                   line_color=[0, 140, 255],
                                   ema_period_alpha=2)

# for image_name in os.listdir("test_images/"):
#     if image_name == '.DS_Store':
#         continue
#     result = pipeline_context.process_image(mpimg.imread('test_images/' + image_name))
#     mpimg.imsave("RENDERED_" + image_name, result)

# pipeline_context.process_video('solidWhiteRight.mp4', 'white.mp4')

# yellow.mp4
pipeline_context = PipelineContext(gaussian_kernel_size=3, canny_low_threshold=50, canny_high_threshold=150,
                                   region_bottom_offset=55,
                                   region_vertice_weights=np.array([(1, 1), (0.48, 0.61), (0.54, 0.60), (1, 1)]),
                                   hough_transform_pipeline=HoughTransformPipeline(rho=2, theta=np.pi / 180,
                                                                                   threshold=20,
                                                                                   min_line_length=50,
                                                                                   max_line_gap=200),
                                   line_color=[0, 140, 255],
                                   ema_period_alpha=1)

# pipeline_context.process_video('solidYellowLeft.mp4', 'yellow.mp4')

# extra.mp4
pipeline_context = PipelineContext(gaussian_kernel_size=3, canny_low_threshold=50, canny_high_threshold=150,
                                   colorspace='hsv',
                                   region_bottom_offset=55,
                                   region_vertice_weights=np.array(
                                       [(1, 0.95), (0.40, 0.65), (0.60, 0.65), (1, 0.935)]),
                                   hough_transform_pipeline=HoughTransformPipeline(rho=2, theta=np.pi / 180,
                                                                                   threshold=20,
                                                                                   min_line_length=15,
                                                                                   max_line_gap=350),
                                   line_color=[0, 140, 255],
                                   ema_period_alpha=2)

pipeline_context.process_video('challenge.mp4', 'extra.mp4')
