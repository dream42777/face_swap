import numpy as np
from PIL import Image
import cv2
import torch
import os
import io
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import StreamingResponse
import uvicorn

from utils.inference.image_processing import crop_face, get_final_image, show_images
from utils.inference.video_processing import read_video, get_target, get_final_video, add_audio_from_another_video, face_enhancement
from utils.inference.core import model_inference

from network.AEI_Net import AEI_Net
from coordinate_reg.image_infer import Handler
from insightface_func.face_detect_crop_multi import Face_detect_crop
from arcface_model.iresnet import iresnet100
from models.pix2pix_model import Pix2PixModel
from models.config_sr import TestOptions
import warnings
warnings.filterwarnings("ignore")


app = Face_detect_crop(name='antelope', root='./insightface_func/models')
app.prepare(ctx_id= 0, det_thresh=0.6, det_size=(640,640))

# main model for generation
G = AEI_Net(backbone='unet', num_blocks=2, c_id=512)
G.eval()
G.load_state_dict(torch.load('weights/G_unet_2blocks.pth', map_location=torch.device('cpu')))
G = G.cuda()
G = G.half()

# arcface model to get face embedding
netArc = iresnet100(fp16=False)
netArc.load_state_dict(torch.load('arcface_model/backbone.pth'))
netArc=netArc.cuda()
netArc.eval()

# model to get face landmarks
handler = Handler('./coordinate_reg/model/2d106det', 0, ctx_id=0, det_size=640)

# model to make superres of face, set use_sr=True if you want to use super resolution or use_sr=False if you don't
os.environ['CUDA_VISIBLE_DEVICES'] = '0'
torch.backends.cudnn.benchmark = True
opt = TestOptions()
#opt.which_epoch ='10_7'
model = Pix2PixModel(opt)
model.netG.train()


app = FastAPI()
# app.add_middleware(
#     CORSMiddleware,
#     allow_origins=["*"],
#     allow_credentials=True,
#     allow_methods=["*"],
#     allow_headers=["*"],
# )

@app.get("/")
async def root():
    return "connection is okay."


@app.post("/upload-image/")
async def upload_image(source_image: UploadFile = File(...), target_image: UploadFile = File(...)):
    source_contents = await source_image.read()
    target_contents = await target_image.read()

    source_full = cv2.cvtColor(np.array(Image.open(io.BytesIO(source_contents))), cv2.COLOR_RGB2BGR)
    target_full = cv2.cvtColor(np.array(Image.open(io.BytesIO(target_contents))), cv2.COLOR_RGB2BGR)

    crop_size = 224 # don't change this

    try:
        source = crop_face(source_full, app, crop_size)[0]
        source = [source[:, :, ::-1]]
        print("Everything is ok!")
    except TypeError:
        print("Bad source images")

    full_frames = [target_full]
    target = get_target(full_frames, app, crop_size)

    final_frames_list, crop_frames_list, full_frames, tfm_array_list = model_inference(full_frames,source,target,netArc,G,app,set_target = False,crop_size=crop_size,BS=1)
    final_frames_list = face_enhancement(final_frames_list, model)

    result = get_final_image(final_frames_list, crop_frames_list, full_frames[0], tfm_array_list, handler)

    result_image = Image.fromarray(cv2.cvtColor(result, cv2.COLOR_BGR2RGB))

    buf = io.BytesIO()
    result_image.save(buf, format='PNG')
    buf.seek(0)

    # Return a confirmation message or any result you want
    return StreamingResponse(buf, media_type="image/png")



if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=5091, reload=True)

