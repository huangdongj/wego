# -*- coding: utf-8 -*-
from exceptions import WegoApiError, WeChatUserError
import wego
import json
import time
import random
import string
import hashlib


class WegoApi(object):
    """
    Wego api dead simple for humans.
    """

    def __init__(self, settings):

        self.settings = settings
        self.wechat = wego.WeChatApi(settings)

    def login_required(self, func):
        """
        Decorator：use for request function, and it will init an independent WegoApi instance.
        """

        def get_wx_user(request, *args, **kwargs):
            """
            Called by login_required, it will set some attributes to function`s first param.

            :param request: Function`s first param.
            :return: Subject to availability.
            """

            helper = self.settings.HELPER(request)

            code = helper.get_params().get('code', '')
            if code:
                openid = self.get_openid(helper, code)
                helper.set_session('wx_openid', openid)

            openid = helper.get_session('wx_openid')
            if openid:
                self.openid = openid
                request.wego = self
                request.wx_openid = openid

                wx_user = self.get_userinfo(helper)
                if wx_user != 'error':
                    request.wx_user = wx_user
                    return func(request, *args, **kwargs)

            return self.redirect_for_code(helper)

        return get_wx_user

    def redirect_for_code(self, helper):
        """
        Let user jump to wechat authorization page.

        :return: Redirect object
        """

        redirect_url = helper.get_current_path()
        url = self.wechat.get_code_url(redirect_url)

        return helper.redirect(url)

    def get_openid(self, helper, code):
        """
        Get user openid.

        :param code: A code that user redirect back will bring.
        :return: openid
        """

        data = self.wechat.get_access_token(code)

        self._set_user_tokens(helper, data)

        return data['openid']

    def get_userinfo(self, helper):
        """
        Get user info.

        :return: :class:`WeChatUser <wego.api.WeChatUser>` object
        """

        wechat_user = self._get_userinfo_from_session(helper)
        if wechat_user:
            return wechat_user

        if helper.get_session('wx_access_token_expires_at') < time.time():
            refresh_token = helper.get_session('wx_refresh_token')
            new_token = self.wechat.refresh_access_token(refresh_token)
            if new_token == 'error':
                return 'error'
            self._set_user_tokens(helper, new_token)

        access_token = helper.get_session('wx_access_token')
        data = self.wechat.get_userinfo_by_token(self.openid, access_token)
        self._set_userinfo_to_session(helper,data)

        return WeChatUser(self, data)

    def _get_userinfo_from_session(self, helper):
        """
        Get user info from session.

        :return: None or :class:`WeChatUser <wego.api.WeChatUser>` object
        """

        if self.settings.USERINFO_EXPIRE:
            wx_userinfo = helper.get_session('wx_userinfo')
            if wx_userinfo:
                wx_userinfo = dict({'expires_at': 0}, **json.loads(wx_userinfo))
                if wx_userinfo['expires_at'] > time.time():
                    return WeChatUser(self, wx_userinfo)
        return None

    def _set_userinfo_to_session(self, helper, data):
        """
        Set user info into session.

        :param data: user info.
        :return: None
        """

        data['expires_at'] = time.time() + self.settings.USERINFO_EXPIRE
        helper.set_session('wx_userinfo', json.dumps(data))

    def _set_user_tokens(self, helper, data):
        """
        Set user all tokens to sessions.

        :param data: Tokens.
        :return: None
        """

        helper.set_session('wx_access_token', data['access_token'])
        helper.set_session('wx_access_token_expires_at', time.time() + data['expires_in'] - 180)
        helper.set_session('wx_refresh_token', data['refresh_token'])

    def get_unifiedorder_info(self, **kwargs):
        """ 
        Unifiedorder settings, get wechat config at https://api.mch.weixin.qq.com/pay/unifiedorder
        You can take return value as wechat api onBridgeReady's parameters directly

        You don't need to include appid, mch_id, nonce_str and sign because these three parameters set by WeChatApi,
        but the following parameters are necessary, you must be included in the kwargs
        and you must follow the format below as the parameters's key

        :param openid: User openid.

        :param body: Goods are simply described, the field must be in strict accordance with the
         specification, specific see parameters

        :param out_trade_no: Merchants system internal order number, within 32 characters,
         can include letters, other see merchant order number

        :param total_fee: Total amount of orders, the unit for points, as shown in the payment amount

        :param spbill_create_ip: APP and web payment submitted to client IP, Native fill call
         WeChat payment API machine IP.

        :param notify_url: (optional) Default is what you set at init. Receive pay WeChat asynchronous notification callback address,
         notify the url must be accessible url directly, cannot carry parameters.

        :param trade_type: Values are as follows: the JSAPI, NATIVE APP, details see parameter regulation

        :return: {'appId': string,
                'timeStamp': value,
                'nonceStr': value,
                'package': value,
                'signType': value,
                'paySign': value,}
        """

        default_settings = {
            'appid': self.settings.APP_ID,
            'mch_id': self.settings.MCH_ID,
            'nonce_str': self._get_random_code(),
            'notify_url': self.settings.PAY_NOTIFY_URL,
            'trade_type': 'JSAPI',
        }

        data = dict(default_settings, **kwargs)
        if self.settings.DEBUG:
            data['total_fee'] = 1
        data['sign'] = self.make_sign(data)

        self._check_params(
            data,
            'appid',
            'mch_id',
            'nonce_str',
            'body',
            'out_trade_no',
            'total_fee',
            'spbill_create_ip',
            'notify_url',
            'trade_type')

        order_info = self.wechat.get_unifiedorder(data)

        data = {
            'appId': order_info['appid'],
            'timeStamp': str(int(time.time())),
            'nonceStr': order_info['nonce_str'],
            'package': 'prepay_id=' + order_info['prepay_id'],
            'signType': 'MD5'
        }
        data['paySign'] = self.make_sign(data)

        return data


    def get_order_query(self, out_trade_no=None, transaction_id=None):
        """
        Order query setting, get wechat config at https://api.mch.weixin.qq.com/pay/orderquery
        Choose one in out_trade_no and transaction_id as parameter pass to this function

        :param out_trade_no | transaction_id: WeChat order number, priority in use. Merchants system internal order number, when didn't provide transaction_id need to pass this.

        :return: {...}
        """

        default_settings = {
            'appid': self.settings.APP_ID,
            'mch_id': self.settings.MCH_ID,
            'nonce_str': self._get_random_code(),
        }
        if transaction_id is None:
            default_settings['out_trade_no'] = out_trade_no
        elif out_trade_no is None:
            default_settings['transaction_id'] = transaction_id
        else:
            raise WegoApiError('Missing required parameters "{param}" (缺少必须的参数 "{param}")'.format(param='out_trade_no|transaction_id'))

        default_settings['sign'] = self.make_sign(default_settings)
        data = self.wechat.get_orderquery(default_settings)

        return data

    def close_order(self, out_trade_no):
        """
        Close order, get wechat config at https://api.mch.weixin.qq.com/pay/closeorder

        :param out_trade_no: Merchant order number within the system

        :return: {...}
        """

        data = {
            'appid': self.settings.APP_ID,
            'mch_id': self.settings.MCH_ID,
            'nonce_str': self._get_random_code(),
            'out_trade_no': out_trade_no,
        }
        data['sign'] = self.make_sign(data)
        data = self.wechat.close_order(data)

        return data

    def refund(self, **kwargs):
        """
        Merchant order number within the system, get wechat config at https://api.mch.weixin.qq.com/secapi/pay/refund

        Following parameters are necessary, you must be included in the kwargs and you must follow the format below as the parameters's key

        :param out_trade_no | transaction_id: WeChat order number, priority in use. Merchants system internal order number, when didn't provide transaction_id need to pass this.

        :param out_refund_no: Merchants system within the refund number, merchants within the system, only the same refund order request only a back many times

        :param total_fee: Total amount of orders, the unit for points, only as an integer, see the payment amount

        :param refund_fee: Refund the total amount, total amount of the order, the unit for points, only as an integer, see the payment amount

        :param op_user_id: Operator account, the default for the merchants

        :return: {...}
        """

        default_settings = {
            'appid': self.settings.APP_ID,
            'mch_id': self.settings.MCH_ID,
            'nonce_str': self._get_random_code(),
        }
        try:
            param = kwargs['op_user_id']
        except:
            kwargs['op_user_id'] = self.settings.MCH_ID
 
        data = dict(default_settings, **kwargs)
        if self.settings.DEBUG:
            data['total_fee'] = 1
        data['sign'] = self.make_sign(data)
        self._check_params(
            data,
            'appid',
            'mch_id',
            'nonce_str',
            'sign',
            'out_refund_no',
            'total_fee',
            'refund_fee',
            'op_user_id')
        try:
            param1 = kwargs['out_trade_no']
        except:
            try:
                param2 = kwargs['transaction_id']
            except:
                raise WegoApiError('Missing required parameters "{param}" (缺少必须的参数 "{param}")'.format(param='out_trade_on|transaction_id'))

        data = self.wechat.refund(data)

        return data

    def refund_query(self, **kwargs):
        """
        get wechat config at https://api.mch.weixin.qq.com/pay/refundquery

        :param transaction_id | out_trade_no | out_refund_no | refund_id: One out of four
        :return: dict {...}
        """

        default_settings = {
            'appid': self.settings.APP_ID,
            'mch_id': self.settings.MCH_ID,
            'nonce_str': self._get_random_code(),
        }

        # check param
        flag = False
        keys = ['transaction_id', 'out_trade_no', 'out_refund_no', 'refund_id']
        for k, v in kwargs.items():
            if k in keys:
                flag = True
                break
        if not flag:
            raise WegoApiError('Missing required parameters "{param}" (缺少必须的参数 "{param}")'.format(param='out_trade_on|transaction_id|out_refund_no|refund_id'))

        data = dict(default_settings, **kwargs)
        data['sign'] = self.make_sign(data)
        data = self.wechat.refund_query(data)

        return data

    def download_bill(self, **kwargs):
        """
        get wechat config at https://api.mch.weixin.qq.com/pay/downloadbill

        :param bill_date:

        :param bill_type:

        :return: dict {...}
        """

        default_settings = {
            'appid': self.settings.APP_ID,
            'mch_id': self.settings.MCH_ID,
            'nonce_str': self._get_random_code(),
        }

        data = dict(default_settings, **kwargs)
        data['sign'] = self.make_sign(data)
        self._check_params(
            data,
            'appid',
            'mch_id',
            'nonce_str',
            'sign',
            'bill_date',
            'bill_type')

        data = self.wechat.download_bill(data)

        return data

    def report(self, **kwargs):
        """
        get wechat config at https://api.mch.weixin.qq.com/payitil/report

        :param interface_url:
        :param execute_time:
        :param return_code:
        :param result_code:
        :param user_ip:
        :return: dict{...}
        """

        default_settings = {
            'appid': self.settings.APP_ID,
            'mch_id': self.settings.MCH_ID,
            'nonce_str': self._get_random_code(),
        }

        data = dict(default_settings, **kwargs)
        data['sign'] = self.make_sign(data)

        self._check_params(
            data,
            'appid',
            'mch_id',
            'nonce_str',
            'sign',
            'interface_url',
            'execute_time',
            'return_code',
            'result_code',
            'user_ip'
        )

        data = self.wechat.report(data)

        return data
        
 
    def _check_params(self, params, *args):
        """
        Check if params is available

        :param params: a dict.
        :return: None
        """

        for i in args:
            if i not in params or not params[i]:
                raise WegoApiError('Missing required parameters "{param}" (缺少必须的参数 "{param}")'.format(param=i))

    def _get_random_code(self):
        """
        Get random code
        """

        return reduce(lambda x,y: x+y, [random.choice(string.printable[:62]) for i in range(32)])

    def make_sign(self, data):
        """
        Generate wechat pay for signature
        """

        temp = ['%s=%s' % (k, data[k]) for k in sorted(data.keys())]
        temp.append('key=' + self.settings.MCH_SECRET)
        temp = '&'.join(temp)
        md5 = hashlib.md5()
        md5.update(temp.encode('utf-8'))

        return md5.hexdigest().upper()
   
    def create_group(self, name):
        """
        Create a new group.

        :param name: Group name.
        :return: :dict: {'id': 'int', 'name':'str'}
        """

        return self.wechat.create_group(name)['group']

    def get_groups(self):
        """
        Get all groups.

        :return: :dict: {'your_group_id': {'name':'str', 'count':'int'}}
        """

        data = self.wechat.get_all_groups()
        return {i.pop('id'): i for i in data['groups']}

    def _get_groupid(self, group):
        """
        Input group id or group name and return group id.

        :param group: Group name or group id.
        :return: group id
        """

        groups = self.get_groups()
        if type(group) is int:
            groupid = int(group)
        else:
            group = str(group)
            for i in groups:
                if groups[i]['name'] == group:
                    groupid = i
                    break
            else:
                raise WegoApiError(u'Without this group(没有这个群组)')

        if not groups.has_key(groupid):
            raise WegoApiError(u'Without this group(没有这个群组)')

        return groupid

    def change_group_name(self, group, name):
        """
        Change group name.

        :param group: Group id or group name.
        :param name: New group name
        :return: :Bool
        """

        groupid = self._get_groupid(group)
        data = self.wechat.change_group_name(groupid, name)
        return not data['errcode']
    
    def change_user_group(self, group):
        """
        Change user group.

        :param group: Group id or group name.
        :return: :Bool .
        """

        groupid = self._get_groupid(group)
        data = self.wechat.change_user_group(self.openid, groupid)
        return not data['errcode']

    def del_group(self, group):
        """
        Delete group.

        :param group: Group id or group name.
        :return: :Bool
        """

        groupid = self._get_groupid(group)
        data = self.wechat.del_group(groupid)
        return not data['errcode']

    def create_menu(self, *args, **kwargs):
        """
        Create menu by wego.button

        :return: :Bool
        """

        data = {
            'button': [i.json for i in args]
        }

        if kwargs.has_key('match'):
            data['matchrule'] = kwargs['match'].json
            data = self.wechat.create_conditional_menu(data)
        else:
            data = self.wechat.create_menu(data)

        return not data['errcode'] if data.has_key('errcode') else data['menuid']

    def get_menus(self):

        data = self.wechat.get_menus()
        if data.has_key('errcode') and data['errcode'] == 46003:
            return {'menu':{}}
        return data

    def del_menu(self, target='all'):

        if target == 'all':
            return not self.wechat.del_all_menus()['errcode']

        return not self.wechat.del_conditional_menu(int(target))['errcode']

    def analysis_push(self, raw_xml):
        """
        Analysis xml to dict and set wego push type.
        Wego defind WeChatPush type:
            -- msg --
            text ✓
            image ✓
            voice ✓
            video ✓
            shortvideo ✓
            location ✓
            link ✓
            -- event --
            subscribe ✓
            unsubscribe
            scancode_push
            scancode_waitmsg ✓
            scan
            scan_subscribe
            user_location ✓
            click ✓
            view

        :param raw_xml: Raw xml.
        :return: :class:`WeChatPush <wego.api.WeChatPush>` object.
        :rtype: WeChatPush.
        """

        data = self.wechat._analysis_xml(raw_xml)

        return WeChatPush(data)






    def add_material(self, **kwargs):

        data = self.wechat.add_material(**kwargs)

        return data

    def get_material(self, media_id):

        data = self.wechat.get_material(media_id)

        return data

    def delete_material(self, media_id):

        data = self.wechat.delete_material(media_id)

        return data

    def update_material(self, **kwargs):

        data = self.wechat.update_material(**kwargs)

        return data

    def get_materials_count(self):

        data = self.wechat.get_materials_count()

        return data

    def get_materials_list(self, material_type, offset, count):

        data = self.wechat.get_materials(material_type, offset, count)

        return data

    def create_qrcode(self, key, expire=None):

        if expire:
            data = self.wechat.create_scene_qrcode(key, expire)

        elif type(key) is str:
            data = self.wechat.create_limit_scene_qrcode(key)

        else:
            data = self.wechat.create_limit_str_scene_qrcode(key)

        # TODO 容错
        data['code_url'] = 'https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket=' + data['ticket']
        return data

    def create_short_url(self, url):

        data = self.wechat.create_short_url(url)

        # TODO 容错
        return data['short_url']


class WeChatPush(object):
    """
    """

    def __init__(self, data):

        self.data = data

        if data['MsgType'] == 'event':
            if data['Event'] == 'subscribe' and data.has_key('Ticket'):
                self.type = 'scan_subcribe'
            elif data['Event'] == 'LOCATION':
                self.type = 'user_location'
            else:
                self.type = data['Event'].lower()
        else:
            self.type = data['MsgType']
        self.from_user = data['FromUserName']
        self.to_user = data['ToUserName']

    def reply_text(self, text):

        return wego.wechat.WeChatApi._make_xml({
            'ToUserName': self.from_user,
            'FromUserName': self.to_user,
            'CreateTime': int(time.time()),
            'MsgType': 'text',
            'Content': str(text)
        })

    def reply_image(self, image):

        return wego.wechat.WeChatApi._make_xml({
            'ToUserName': self.from_user,
            'FromUserName': self.to_user,
            'CreateTime': int(time.time()),
            'MsgType': 'image',
            'Image': {'MediaId': image}
        })

    def reply_voice(self, voice):

        return wego.wechat.WeChatApi._make_xml({
            'ToUserName': self.from_user,
            'FromUserName': self.to_user,
            'CreateTime': int(time.time()),
            'MsgType': 'voice',
            'Voice': {'MediaId': voice}
        })

    def reply_video(self, video):

        # TODO 视频要等审核通过才能用, 或者是永久素材
        data = {
            'MediaId': video['media_id'] 
        }
        if video.has_key('title'):
            data['Title'] = video['title']
        if video.has_key('description'):
            data['Description'] = video['description']

        return wego.wechat.WeChatApi._make_xml({
            'ToUserName': self.from_user,
            'FromUserName': self.to_user,
            'CreateTime': int(time.time()),
            'MsgType': 'video',
            'Video': data
        })

    def reply_music(self, music):

        data  = {
            'Title': music['title'],
            'Description': music['description'],
            'MusicUrl': music['music_url'],
            'HQMusicUrl': music['hq_music_url'],
        }
        if music.has_key('thumb_media_id'):
            data['ThumbMediaId'] = music['thumb_media_id']

        return wego.wechat.WeChatApi._make_xml({
            'ToUserName': self.from_user,
            'FromUserName': self.to_user,
            'CreateTime': int(time.time()),
            'MsgType': 'music',
            'Music': data
        })

    def reply_news(self, news):

        data = []
        for i in news:
            new_dict = {}
            if i.has_key('title'):
                new_dict['Title'] = i['title'],
            if i.has_key('description'):
                new_dict['Description'] = i['description'],
            if i.has_key('pic_url'):
                new_dict['PicUrl'] = i['pic_url'],
            if i.has_key('url'):
                new_dict['Url'] = i['url'],
            data.append(new_dict)

        return wego.wechat.WeChatApi._make_xml({
            'ToUserName': self.from_user,
            'FromUserName': self.to_user,
            'CreateTime': int(time.time()),
            'MsgType': 'news',
            'ArticleCount': len(news),
            'Articles': {
                'item': data
            }
        })


class WeChatUser(object):
    """
    A lazy and smart wechat user object. You can set user remark, group, groupid direct,
    because of group name can be repeated, so if you set the group by group name, it may not be accurate.
    """

    def __init__(self, wego, data):
        
        self.wego = wego
        self.data = data
        self.is_upgrade = False

    def __getattr__(self, key):

        ext_userinfo = ['subscribe', 'language', 'remark', 'groupid']
        if key in ext_userinfo and not self.is_upgrade:
            self.get_ext_userinfo()

        if key == 'group' and not self.data.has_key(key):
            self.data['group'] = self.wego.get_groups()[self.groupid]

        if self.data.has_key(key):
            return self.data[key]
        return ''

    def __setattr__(self, key, value):
        
        if key == 'remark':
            if self.subscribe != 1:
                raise WeChatUserError('The user does not subscribe you')

            if self.data['remark'] != value:
                self.wego.wechat.set_user_remark(self.wego.openid, value)
                self.data[key] = value

        if key in ['group', 'groupid']:
            groups = self.wego.get_groups()
            if key == 'group':
                for i in groups:
                    if groups[i]['name'] == value:
                        value = i
                        break
                else:
                    raise WeChatUserError(u'Without this group(没有这个群组)')

            groupid = value 
            if not groups.has_key(groupid):
                raise WeChatUserError(u'Without this group(没有这个群组)')

            self.wego.change_user_group(groupid)
        
        super(WeChatUser, self).__setattr__(key, value)

    def get_ext_userinfo(self):
        """
        Get user extra info, such as subscribe, language, remark and groupid.

        :return: :dict: User data
        """

        self.data['remark'] = ''
        self.data['groupid'] = ''

        data = self.wego.wechat.get_userinfo(self.wego.openid)
        self.data = dict(self.data, **data)
        self.is_upgrade = True

        return self.data


# TODO 更方便定制
def official_get_global_access_token(self):
    """
    Get global access token.

    :param self: Call self.get_global_access_token() for get global access token.
    :return: :str: Global access token
    """

    if not self.global_access_token or self.global_access_token['expires_in'] <= int(time.time()):
        self.global_access_token = self.get_global_access_token()
        self.global_access_token['expires_in'] += int(time.time()) - 180

    return self.global_access_token['access_token']