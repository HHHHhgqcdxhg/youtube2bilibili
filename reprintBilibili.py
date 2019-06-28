import base64
import datetime
import json
import math
import os
import re
import time
from concurrent import futures

import requests
from cqhttp import CQHttp
from requests.adapters import HTTPAdapter

pwd = os.path.dirname(__file__)
configFilePath = os.path.join(pwd, "config.json")

with open(configFilePath, "r", encoding="utf8") as f:
    config = json.load(f)
config["biliCookie"] += ";"


class Utils:
    global print
    oriWrite = print

    bot = CQHttp(api_root=config["cqApiRoot"])

    QQNoticeGroup = config["QQNoticeGroup"]

    @classmethod
    def timeWrite(cls, *args):
        datetimeString = "[" + datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + "]"
        cls.oriWrite(datetimeString, *args)

    @classmethod
    def notice(cls, message):
        cls.bot.send_group_msg(group_id=cls.QQNoticeGroup, message=message)


print = Utils.timeWrite


class Re:
    biliJct = re.compile('bili_jct=(.*?);')
    dedeUserID = re.compile('DedeUserID=(.*?);')
    upos = re.compile(r'^upos://')


class UploadChunkPara:
    @classmethod
    def initClass(cls, upload_session, filePath, total_chunks, uploadId, filesize, uploadUrl):
        cls.upload_session = upload_session
        cls.filePath = filePath
        cls.total_chunks = total_chunks
        cls.uploadId = uploadId
        cls.filesize = filesize
        cls.uploadUrl = uploadUrl


class VideoInfo:
    def __init__(self, url, fulltitle, thumbnail, tags: list, description, _filename,id):
        self.url = url
        self.fulltitle = fulltitle
        self.thumbnail = thumbnail
        self.tags = tags
        self.description = description
        self._filename = _filename
        self.id = id

    def __str__(self) -> str:
        return self.__dict__.__str__()


class UploadBili():
    _profile = 'ugcupos/yb'
    _cdn = 'ws'
    _CHUNK_SIZE = 4 * 1024 * 1024

    def __init__(self):
        self._config = config
        self._initPara()
        self._session = self._initSession()

    def _initPara(self):
        self._MAX_RETRYS = self._config["MAX_RETRYS"]
        self._csrf = Re.biliJct.search(self._config["biliCookie"]).group(1)
        self._mid = Re.dedeUserID.search(self._config["biliCookie"]).group(1)

    def _initSession(self):
        session = requests.session()
        session.mount('https://', HTTPAdapter(max_retries=self._MAX_RETRYS))
        session.headers['cookie'] = self._config["biliCookie"]
        session.headers['Accept'] = 'application/json, text/javascript, */*; q=0.01'
        session.headers[
            'User-Agent'] = 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/57.0.2987.133 Safari/537.36'
        session.headers['Referer'] = f'https://space.bilibili.com/{self._mid}/#!/'
        return session


    def _downloadCover(self, url, toDirectory):
        print("downloading cover...")
        coverFileName = os.path.split(url)[-1]
        toPath = os.path.join(toDirectory, coverFileName)

        coverContent = requests.get(url).content

        with open(toPath, "wb") as f:
            f.write(coverContent)

        print(f"cover downloaded to {toPath}")
        return toPath

    def _uploadVideo(self, filepath):
        """执行上传文件操作"""
        if not os.path.isfile(filepath):
            filePathPart = os.path.split(filepath)
            videoFileName = ".".join(filePathPart[-1].split(".")[:-1])
            for fileName in os.listdir(filePathPart[0]):
                if ".".join(fileName.split(".")[:-1]) == videoFileName:
                    filepath = os.path.join(filePathPart[0], fileName)
                    break
            else:
                print(f'FILE NOT EXISTS: {filepath}')
                return

        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)

        # 1.获取本次上传所需信息
        preupload_url = 'https://member.bilibili.com/preupload'
        params = {
            'os': 'upos',
            'r': 'upos',
            'ssl': '0',
            'name': filename,
            'size': filesize,
            'upcdn': self._cdn,
            'profile': self._profile,
        }
        response = self._session.get(preupload_url, params=params)
        upload_info = response.json()

        # 本次上传bilibili端文件名
        upload_info['bili_filename'] = upload_info['upos_uri'].split('/')[-1].split('.')[0]
        # 本次上传url
        endpoint = 'http:%s/' % upload_info['endpoint']
        upload_url = Re.upos.sub(endpoint, upload_info['upos_uri'])
        # 本次上传session
        upload_session = requests.session()
        upload_session.mount('http://', HTTPAdapter(max_retries=self._MAX_RETRYS))
        upload_session.headers['X-Upos-Auth'] = upload_info['auth']

        # 2.获取本次上传的upload_id
        response = upload_session.post(upload_url + '?uploads&output=json')
        upload_info['upload_id'] = response.json()['upload_id']

        # 3.分块上传文件
        total_chunks = math.ceil(filesize * 1.0 / self._CHUNK_SIZE)
        parts_info = {'parts': [{'partNumber': i + 1, 'eTag': 'etag'} for i in range(total_chunks)]}

        print("uploading Video...")

        UploadChunkPara.initClass(upload_session, filepath, total_chunks, upload_info['upload_id'], filesize,
                                  upload_url)
        self._uploadChunkPara = UploadChunkPara()
        chunkNos = list(range(total_chunks))

        with futures.ThreadPoolExecutor(max_workers=self._config["maxWorks"]) as executor:
            results = executor.map(self.uploadChunk, chunkNos)

        # 4.标记本次上传完成
        params = {
            'output': 'json',
            'name': filename,
            'profile': self._profile,
            'uploadId': upload_info['upload_id'],
            'biz_id': upload_info['biz_id']
        }
        response = upload_session.post(upload_url, params=params, data=parts_info)
        Utils.notice(message="上传视频完成")
        return upload_info

    def _cover_up(self, image_path):
        """上传图片并获取图片链接"""
        if not os.path.isfile(image_path):
            return ''
        fp = open(image_path, 'rb')
        encode_data = base64.b64encode(fp.read())
        url = 'https://member.bilibili.com/x/vu/web/cover/up'
        data = {
            'cover': b'data:image/jpeg;base64,' + encode_data,
            'csrf': self._csrf,
        }
        response = self._session.post(url, data=data)
        return response.json()['data']['url']

    def _cover_default(self, filename, timeout):
        if timeout == 0:
            print("NO COVER")
            return ""
        time.sleep(5)
        url = f'https://member.bilibili.com/x/web/archive/recovers?fns={filename}'
        response = self._session.get(url)
        if len(response.json()['data']) == 0:
            print("GENERATE COVER, WAIT 5 SEC")
            return self._cover_default(filename, timeout - 1)
        else:
            cover_url = response.json()['data'][0]
            print(f'DEFAULT COVER: {cover_url}')
            return cover_url

    def upload(self, filepath, title, tid, tag: list = "", desc='', source='', cover_path='', dynamic='', no_reprint=1):
        """视频投稿
        Args:
            filepath   : 视频文件路径
            title      : 投稿标题
            tid        : 投稿频道id,详见https://member.bilibili.com/x/web/archive/pre
            tag        : 视频标签，多标签使用','号分隔
            desc       : 视频描述信息
            source     : 转载视频出处url
            cover_path : 封面图片路径
            dynamic    : 分享动态, 比如："#周五##放假# 劳资明天不上班"
            no_reprint : 1表示不允许转载,0表示允许
        """
        title = title[:80]
        desc = desc[:233]
        tag = tag[:10]

        # 上传文件, 获取上传信息
        upload_info = self._uploadVideo(filepath)
        if not upload_info:
            return
        # 获取图片链接
        cover_url = self._cover_up(cover_path) if cover_path else self._cover_default(upload_info["bili_filename"], 20)
        # 版权判断, 转载无版权
        copyright = 2 if source else 1
        # tag设置
        if isinstance(tag, list):
            tag = ','.join(tag)
        # 设置视频基本信息
        params = {
            'copyright': copyright,
            'source': source,
            'title': title,
            'tid': tid,
            'tag': tag,
            'no_reprint': no_reprint,
            'desc': desc,
            'desc_format_id': 0,
            'dynamic': dynamic,
            'cover': cover_url,
            'videos': [{
                'filename': upload_info['bili_filename'],
                'title': title,
                'desc': '',
            }]
        }
        if source:
            del params['no_reprint']
        url = f'https://member.bilibili.com/x/vu/web/add?csrf={self._csrf}'
        response = self._session.post(url, json=params)
        print('SET VIDEO INFO')
        SuccessData = response.json()
        Utils.notice(message=f"上传工作全部完成{SuccessData}")
        return SuccessData

    def uploadChunk(self, chunkNo: int):
        print(f"start upload chunk {chunkNo} ... (total {self._uploadChunkPara.total_chunks})")
        chunkNo = chunkNo
        offset = self._CHUNK_SIZE * chunkNo

        with open(f"{self._uploadChunkPara.filePath}", "rb") as fp:
            fp.seek(self._CHUNK_SIZE * chunkNo)
            blob = fp.read(self._CHUNK_SIZE)

        params = {
            'partNumber': chunkNo + 1,
            'uploadId': self._uploadChunkPara.uploadId,
            'chunk': chunkNo,
            'chunks': self._uploadChunkPara.total_chunks,
            'size': len(blob),
            'start': offset,
            'end': offset + len(blob),
            'total': self._uploadChunkPara.filesize,
        }
        response = self._uploadChunkPara.upload_session.put(self._uploadChunkPara.uploadUrl, params=params, data=blob,
                                                            timeout=1200)
        print(f'    chunk {chunkNo} uploaded')
        return response


class DownloadY2b():

    @staticmethod
    def getVideoInfo(url):
        for i in range(config["MAX_RETRYS"]):
            try:
                cmd = f"""{config["youtubeDlPath"]} -s -j {url}"""
                resStr: str = os.popen(cmd).read()
                resDict = json.loads(resStr, encoding="utf8")
                v = VideoInfo(url, resDict["fulltitle"], resDict["thumbnail"], resDict["tags"], resDict["description"],
                              resDict["_filename"],resDict["id"])
                return v
            except Exception as e:
                print(str(e))

    @staticmethod
    def downloadVideo(url, toPath):
        if (os.path.exists(toPath)):
            os.remove(toPath)
        cmd = f"""{config["youtubeDlPath"]} -o {toPath} {url}"""
        os.system(cmd)
        return toPath

    @staticmethod
    def downloadCover(url, saveDir):
        fileName = os.path.split(url)[-1]
        savePath = os.path.join(saveDir, fileName)
        res = requests.get(url)
        content = res.content
        with open(savePath, "wb") as f:
            f.write(content)
        return savePath

    @staticmethod
    def prepareVideoPath(tmpDir,videoInfo:VideoInfo):
        videoId = videoInfo.id
        findExist = DownloadY2b.findFile(tmpDir,videoId)

        if findExist:
            if(os.path.isfile(findExist)):
                os.remove(findExist)
            elif os.path.isdir(findExist):
                os.rmdir(findExist)

        # 这里的toPath是不含文件后缀的path
        toPath = os.path.join(tmpDir,videoId)
        return toPath

    @staticmethod
    def findFile(path,videoId):
        files = os.listdir(path)
        for file in files:
            fileName = file.split(".")[0]
            if videoId == fileName:
                return os.path.join(path,file)
        return False

def doCallback(callBackData):
    pass


def handdleNewY2bVideo(url, callBack=doCallback):
    videoInfo = DownloadY2b.getVideoInfo(url)
    print(videoInfo)

    videoPathWithoutExt = DownloadY2b.prepareVideoPath(config["tmpVideoPath"],videoInfo)
    videoPathWithoutExt = DownloadY2b.downloadVideo(url, videoPathWithoutExt)
    videoPathWithExt = DownloadY2b.findFile(config["tmpVideoPath"],videoInfo.id)
    coverPath = DownloadY2b.downloadCover(videoInfo.thumbnail, config["tmpCoverPath"])
    uploadBili = UploadBili()
    print(videoPathWithExt)

    uploadedInfo = uploadBili.upload(filepath=videoPathWithExt, title=videoInfo.fulltitle, tid=172, tag=videoInfo.tags[:10],
                                     desc=videoInfo.description, source=url, cover_path=coverPath)

    os.remove(videoPathWithExt)
    if callBack:
        callBackData = {"videoInfo": videoInfo, "uploadedInfo": uploadedInfo}
        callBack(callBackData)
        return callBackData


if __name__ == '__main__':
    # y2b = UploadBili()
    # y2b.banyunFromY("https://youtu.be/ziOk4JpRy-s")
    # chunkNo = y2b.splitVideoChunk("J:\\test\\tmp.flv")
    # print(chunkNo)
    # res = y2b.upload(r"J:\test\tmp.flv", "稿件标题是八个字", 172, ["碧蓝航线"], "详细描述七个字", "youtu.be/adadfsi6Y", r"J:\test\tmp.png","")
    # print(res)
    handdleNewY2bVideo("https://www.youtube.com/watch?v=9kiZMC3yk3Y")
