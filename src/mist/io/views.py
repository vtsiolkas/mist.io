"""mist.io views"""
import os
import tempfile
import logging

from time import time

from datetime import datetime

import requests
import json

from hashlib import sha256

from pyramid.response import Response
from pyramid.view import view_config

from libcloud.compute.base import Node, NodeSize, NodeImage, NodeLocation
from libcloud.compute.base import NodeAuthSSHKey
from libcloud.compute.deployment import MultiStepDeployment, ScriptDeployment
from libcloud.compute.deployment import SSHKeyDeployment
from libcloud.compute.types import Provider
from libcloud.common.types import InvalidCredsError

from mist.io.config import STATES, SUPPORTED_PROVIDERS
from mist.io.config import EC2_IMAGES, EC2_PROVIDERS, EC2_SECURITYGROUP
from mist.io.config import LINODE_DATACENTERS

from mist.io.helpers import connect
from mist.io.helpers import generate_backend_id, get_machine_actions
from mist.io.helpers import import_key, create_security_group
from mist.io.helpers import get_keypair, get_keypair_by_name, get_preferred_keypairs
from mist.io.helpers import run_command

from mist.io.helpers import generate_keypair, set_default_key, get_private_key, validate_key_pair, get_ssh_user_from_keypair

try:
    from mist.core.helpers import save_settings
except ImportError:
    from mist.io.helpers import save_settings

log = logging.getLogger('mist.io')


@view_config(route_name='home', request_method='GET',
             renderer='templates/home.pt')
def home(request):
    """Gets all the basic data for backends, project name and session status.

    """
    try:
        email = request.environ['beaker.session']['email']
        session = True
    except:
        session = False
        try:
            email = request.registry.settings['email']
            password = request.registry.settings['password']
        except:
            email = ''

    core_uri = request.registry.settings['core_uri']
    auth = request.registry.settings.get('auth', 0)
    js_build = request.registry.settings['js_build']
    js_log_level = request.registry.settings['js_log_level']

    return {'project': 'mist.io',
            'session': session,
            'email': email,
            'supported_providers': SUPPORTED_PROVIDERS,
            'core_uri': core_uri,
            'auth': auth,
            'js_build': js_build,
            'js_log_level': js_log_level}


@view_config(route_name="check_auth", request_method='POST', renderer="json")
def check_auth(request):
    "Check on the mist.core service if authenticated"
    params = request.json_body
    email = params.get('email', '').lower()
    password = params.get('password', '')
    timestamp = params.get('timestamp', '')
    hash_key = params.get('hash', '')

    payload = {'email': email, 'password': password, 'timestamp': timestamp, 'hash_key': hash_key}
    core_uri = request.registry.settings['core_uri']
    ret = requests.post(core_uri + '/auth', params=payload, verify=False)

    if ret.status_code == 200:
        ret = json.loads(ret.content)
        request.registry.settings['email'] = email
        request.registry.settings['password'] = password
        request.registry.settings['auth'] = 1
        log.debug('save settings (check auth)')
        save_settings(request)
        return ret
    else:
        return Response('Unauthorized', 401)

@view_config(route_name='account', request_method='POST', renderer='json')
def update_user_settings(request, renderer='json'):
    """try free plan, by communicating to the mist.core service
    """
    params = request.json_body
    action = params.get('action', '').lower()
    plan = params.get('plan', '')
    auth_key = params.get('auth_key', '')
    name = params.get('name', '')    
    company_name = params.get('company_name', '')
    country = params.get('country', '') 
    number_of_servers = params.get('number_of_servers', '') 
    number_of_people = params.get('number_of_people', '')            

    payload = {'auth_key': auth_key,
               'action': action, 
               'plan': plan, 
               'name': name, 
               'company_name': company_name, 
               'country': country,
               'number_of_servers': number_of_servers,
               'number_of_people': number_of_people
    }

    core_uri = request.registry.settings['core_uri']
    ret = requests.post(core_uri + '/account', params=payload, verify=False)

    if ret.status_code == 200:
        ret = json.loads(ret.content)
        return ret
    else:
        return Response('Unauthorized', 401)




@view_config(route_name='backends', request_method='GET', renderer='json')
def list_backends(request):
    """Gets the available backends.

    .. note:: Currently, this is only used by the backend controller in js.

    """
    try:
        backends = request.environ['beaker.session']['backends']
    except:
        backends = request.registry.settings['backends']

    ret = []
    for backend_id in backends:
        backend = backends[backend_id]
        ret.append({'id': backend_id,
                    'apikey': backend.get('apikey', None),
                    'title': backend.get('title', backend['provider']),
                    'provider': backend['provider'],
                    'poll_interval': backend.get('poll_interval', 10000),
                    'state': 'wait',
                    # for Provider.RACKSPACE_FIRST_GEN
                    'region': backend.get('region', None),
                    # for Provider.RACKSPACE (the new Nova provider)
                    'datacenter': backend.get('datacenter', None),
                    'enabled': backend.get('enabled', True),
                     })

    return ret

   
@view_config(route_name='backends', request_method='POST', renderer='json')
def add_backend(request, renderer='json'):
    try:
        backends = request.environ['beaker.session']['backends']
    except:
        backends = request.registry.settings['backends']
    params = request.json_body
    provider = params.get('provider', '0')['provider']
    apikey = params.get('apikey', '')
    apisecret = params.get('apisecret', '')
    apiurl = params.get('apiurl', '')
    tenant_name = params.get('tenant_name', '')
    if apisecret == 'getsecretfromdb':
        for backend_id in backends:
            backend = backends[backend_id]
            if backend.get('apikey', None) == apikey:
                apisecret = backend.get('apisecret', None)
    region = ''
    if not provider.__class__ is int and ':' in provider:
        region = provider.split(':')[1]
        provider = provider.split(':')[0]

    if not provider or not apikey or not apisecret:
        return Response('Invalid backend data', 400)

    backend_id = generate_backend_id(provider, region, apikey)
    
    if backend_id in backends:
        return Response('Backend exists', 409)

    backend = {'title': params.get('provider', '0')['title'],
               'provider': provider,
               'apikey': apikey,
               'apisecret': apisecret,
               'apiurl': apiurl,
               'tenant_name': tenant_name,
               'region': region,
               'poll_interval': request.registry.settings['default_poll_interval'],
               'enabled': 1,
              }

    request.registry.settings['backends'][backend_id] = backend
    log.debug('save settings (add backend')
    save_settings(request)

    ret = {'id'           : backend_id,
           'apikey'       : backend['apikey'],
           'apiurl'       : backend['apiurl'],
           'tenant_name'  : backend['tenant_name'],
           'title'        : backend['title'],
           'provider'     : backend['provider'],
           'poll_interval': backend['poll_interval'],
           'region'       : backend['region'],
           'status'       : 'off',
           'enabled'      : 1,
          }
    return ret


@view_config(route_name='backend_action', request_method='DELETE',
             renderer='json')
def delete_backend(request, renderer='json'):
    """Deletes a backend.

    .. note:: It assumes the user may re-add it later so it does not remove
              any key associations.

    """
    request.registry.settings['backends'].pop(request.matchdict['backend'])
    log.debug('save settings (del backend)')
    save_settings(request)

    return Response('OK', 200)


@view_config(route_name='machines', request_method='GET', renderer='json')
def list_machines(request):
    """Gets machines and their metadata from a backend.

    Several checks are needed, because each backend stores metadata
    differently.

    The folowing are considered:::

        * For tags, Rackspace stores them in extra.metadata.tags while EC2 in
          extra.tags.tags.
        * For images, both EC2 and Rackpace have an image and an etra.imageId
          attribute
        * For flavors, EC2 has an extra.instancetype attribute while Rackspace
          an extra.flavorId. however we also expect to get size attribute.

    """
    try:
        conn = connect(request)
    except RuntimeError as e:
        log.error(e)
        return Response('Internal server error: %s' % e, 503)
    except:
        return Response('Backend not found', 404)

    try:
        machines = conn.list_nodes()
    except InvalidCredsError:
        return Response('Invalid credentials', 401)
    except:
        return Response('Backend unavailable', 503)

    ret = []
    for m in machines:
        tags = m.extra.get('tags', None) or m.extra.get('metadata', None)
        tags = tags or {}
        tags = [value for key, value in tags.iteritems() if key != 'Name']

        if m.extra.get('availability', None):
            # for EC2
            tags.append(m.extra['availability'])
        elif m.extra.get('DATACENTERID', None):
            # for Linode
            tags.append(LINODE_DATACENTERS[m.extra['DATACENTERID']])

        image_id = m.image or m.extra.get('imageId', None)

        size = m.size or m.extra.get('flavorId', None)
        size = size or m.extra.get('instancetype', None)

        machine = {'id'            : m.id,
                   'uuid'          : m.get_uuid(),
                   'name'          : m.name,
                   'imageId'       : image_id,
                   'size'          : size,
                   'state'         : STATES[m.state],
                   'private_ips'   : m.private_ips,
                   'public_ips'    : m.public_ips,
                   'tags'          : tags,
                   'extra'         : m.extra,
                  }
        machine.update(get_machine_actions(m, conn))
        ret.append(machine)
    return ret


@view_config(route_name='machines', request_method='POST', renderer='json')
def create_machine(request):
    """Creates a new virtual machine on the specified backend.

    If the backend is Rackspace it attempts to deploy the node with an ssh key
    provided in config. the method used is the only one working in the old
    Rackspace backend. create_node(), from libcloud.compute.base, with 'auth'
    kwarg doesn't do the trick. Didn't test if you can upload some ssh related
    files using the 'ex_files' kwarg from openstack 1.0 driver.

    In Linode creation is a bit different. There you can pass the key file
    directly during creation. The Linode API also requires to set a disk size
    and doesn't get it from size.id. So, send size.disk from the client and
    use it in all cases just to avoid provider checking. Finally, Linode API
    does not support association between a machine and the image it came from.
    We could set this, at least for machines created through mist.io in
    ex_comment, lroot or lconfig. lroot seems more appropriate. However,
    liblcoud doesn't support linode.config.list at the moment, so no way to
    get them. Also, it will create inconsistencies for machines created
    through mist.io and those from the Linode interface.

    """
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    backend_id = request.matchdict['backend']

    try:
        key_id = request.json_body['key']
    except:
        key_id = None

    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})

    if key_id:
        keypair = get_keypair_by_name(keypairs, key_id)
    else:
        keypair = get_keypair(keypairs)

    if keypair:
        private_key = keypair['private']
        public_key = keypair['public']
    else:
        private_key = public_key = None

    try:
        machine_name = request.json_body['name']
        location_id = request.json_body.get('location', None)
        image_id = request.json_body['image']
        size_id = request.json_body['size']
        #deploy_script received as unicode, but ScriptDeployment wants str
        script = str(request.json_body.get('script', ''))
        # these are required only for Linode, passing them anyway
        image_extra = request.json_body['image_extra']
        disk = request.json_body['disk']
    except Exception as e:
        return Response('Invalid payload', 400)

    size = NodeSize(size_id, name='', ram='', disk=disk, bandwidth='',
                    price='', driver=conn)
    image = NodeImage(image_id, name='', extra=image_extra, driver=conn)

    if conn.type in EC2_PROVIDERS:
        locations = conn.list_locations()
        for loc in locations:
            if loc.id == location_id:
                location = loc
                break
    else:
        location = NodeLocation(location_id, name='', country='', driver=conn)

    if conn.type in [Provider.RACKSPACE_FIRST_GEN, 
                     Provider.RACKSPACE, 
                     Provider.OPENSTACK] and public_key:
        key = SSHKeyDeployment(str(public_key))
        deploy_script = ScriptDeployment(script)
        msd = MultiStepDeployment([key, deploy_script])
        try:
            node = conn.deploy_node(name=machine_name,
                             image=image,
                             size=size,
                             location=location,
                             deploy=msd)
            associate_key(request, key_id, backend_id, node.id, deploy=False)
        except Exception as e:
            return Response('Failed to create machine in Rackspace: %s' % e, 500)
    elif conn.type in EC2_PROVIDERS and public_key and private_key:
        imported_key = import_key(conn, public_key, key_id)
        created_security_group = create_security_group(conn, EC2_SECURITYGROUP)
        deploy_script = ScriptDeployment(script)

        (tmp_key, tmp_key_path) = tempfile.mkstemp()
        key_fd = os.fdopen(tmp_key, 'w+b')
        key_fd.write(private_key)
        key_fd.close()
        #deploy_node wants path for ssh private key
        if imported_key and created_security_group:
            try:
                node = conn.deploy_node(name=machine_name,
                                 image=image,
                                 size=size,
                                 deploy=deploy_script,
                                 location=location,
                                 ssh_key=tmp_key_path,
                                 ssh_alternate_usernames=['ec2-user', 'ubuntu'],
                                 max_tries=1,
                                 ex_keyname=key_id,
                                 ex_securitygroup=EC2_SECURITYGROUP['name'])
                associate_key(request, key_id, backend_id, node.id, deploy=False)
            except Exception as e:
                return Response('Failed to create machine in EC2: %s' % e, 500)
        #remove temp file with private key
        try:
            os.remove(tmp_key_path)
        except:
            pass
    elif conn.type is Provider.LINODE and public_key and private_key:
        auth = NodeAuthSSHKey(public_key)

        (tmp_key, tmp_key_path) = tempfile.mkstemp()
        key_fd = os.fdopen(tmp_key, 'w+b')
        key_fd.write(private_key)
        key_fd.close()

        deploy_script = ScriptDeployment(script)
        try:
            node = conn.deploy_node(name=machine_name,
                             image=image,
                             size=size,
                             deploy=deploy_script,
                             location=location,
                             auth=auth,
                             ssh_key=tmp_key_path)
            associate_key(request, key_id, backend_id, node.id, deploy=True)
        except Exception as e:
            return Response('Failed to create machine in Linode: %s' % e, 500)
        #remove temp file with private key
        try:
            os.remove(tmp_key_path)
        except:
            pass
    elif conn.type is Provider.DIGITAL_OCEAN and public_key and private_key:
        """
        Adding public key to user's DigitalOcean account, which associates
        a unique numeric id to the key.
        Then we can create a machine which contains the key, by passing
        that numeric id to deploy_node.
        There seems to be no limit to the number of keys, or any problem with
        duplicate key names so we simply add a key to the account in
        DigitalOcean for each machine we create, even if it's already
        there(it will get a different numeric id).
        
        Alternatively, we could check if the public_key is already in our
        DigitalOcean account and use that numeric id, but that needs one
        request per key checked
        """
        account_ssh_key = conn.ex_create_ssh_key(key_id, public_key)

        (tmp_key, tmp_key_path) = tempfile.mkstemp()
        key_fd = os.fdopen(tmp_key, 'w+b')
        key_fd.write(private_key)
        key_fd.close()

        deploy_script = ScriptDeployment(script)

        try:
            node = conn.deploy_node(name=machine_name,
                             image=image,
                             size=size,
                             deploy=deploy_script,
                             location=location,
                             ex_ssh_key_ids = [str(account_ssh_key.id)],
                             ssh_key=tmp_key_path)
            # Pass deploy=False to associate_key because the ssh key is already
            # deployed(internally by DigitalOcean) on the machine.
            associate_key(request, key_id, backend_id, node.id, deploy=False)
        except Exception as e:
            return Response('Failed to create machine in Digital Ocean: %s' % e, 500)
        #remove temp file with private key
        try:
            os.remove(tmp_key_path)
        except:
            pass
    else:
        return Response('Cannot create a machine without a keypair', 400)

    return {'id': node.id,
            'name': node.name,
            'extra': node.extra,
            'public_ips': node.public_ips,
            'private_ips': node.private_ips,
            }


@view_config(route_name='machine', request_method='POST',
             request_param='action=start', renderer='json')
def start_machine(request):
    """Starts a machine on backends that support it.

    Currently only EC2 supports that.

    .. note:: Normally try won't get an AttributeError exception because this
              action is not allowed for machines that don't support it. Check
              helpers.get_machine_actions.

    """
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    machine_id = request.matchdict['machine']
    machine = Node(machine_id,
                   name=machine_id,
                   state=0,
                   public_ips=[],
                   private_ips=[],
                   driver=conn)
    try:
        # In liblcoud it is not possible to call this with machine.start()
        conn.ex_start_node(machine)
        return Response('Success', 200)
    except AttributeError:
        return Response('Action not supported for this machine', 404)
    except:
        return []


@view_config(route_name='machine', request_method='POST',
             request_param='action=stop', renderer='json')
def stop_machine(request):
    """Stops a machine on backends that support it.

    Currently only EC2 supports that.

    .. note:: Normally try won't get an AttributeError exception because this
              action is not allowed for machines that don't support it. Check
              helpers.get_machine_actions.

    """
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    machine_id = request.matchdict['machine']
    machine = Node(machine_id,
                   name=machine_id,
                   state=0,
                   public_ips=[],
                   private_ips=[],
                   driver=conn)

    try:
        # In libcloud it is not possible to call this with machine.stop()
        conn.ex_stop_node(machine)
        return Response('Success', 200)
    except AttributeError:
        return Response('Action not supported for this machine', 404)
    except:
        return []


@view_config(route_name='machine', request_method='POST',
             request_param='action=reboot', renderer='json')
def reboot_machine(request, backend_id=None, machine_id=None):
    """Reboots a machine on a certain backend."""

    if not backend_id:
        try:
            backend_id = request.matchdict['backend']
        except:
            Response('Bad Request', 400)
    try:
        conn = connect(request, backend_id=backend_id)
    except:
        return Response('Backend not found', 404)

    if not machine_id:
        try:
            machine_id = request.matchdict['machine']
        except:
            Response('Bad Request', 400)

    machine = Node(machine_id,
                   name=machine_id,
                   state=0,
                   public_ips=[],
                   private_ips=[],
                   driver=conn)

    machine.reboot()

    return Response('Success', 200)


@view_config(route_name='machine', request_method='POST',
             request_param='action=destroy', renderer='json')
def destroy_machine(request, backend_id=None, machine_id=None):
    """Destroys a machine on a certain backend.

    After destroying a machine it also deletes all key associations. However,
    it doesn't undeploy the keypair. There is no need to do it because the
    machine will be destroyed.

    """
    if not backend_id:
        try:
            backend_id = request.matchdict['backend']
        except:
            Response('Bad Request', 400)
    try:
        conn = connect(request, backend_id=backend_id)
    except:
        return Response('Backend not found', 404)

    if not machine_id:
        try:
            machine_id = request.matchdict['machine']
        except:
            Response('Bad Request', 400)

    machine = Node(machine_id,
                   name=machine_id,
                   state=0,
                   public_ips=[],
                   private_ips=[],
                   driver=conn)

    machine.destroy()

    pair = [backend_id, machine_id]

    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})

    for key in keypairs:
        machines = keypairs[key].get('machines', [])
        for machine in machines:
            if pair==machine[:2]:
                disassociate_key(request, key, backend_id, machine_id, undeploy=False)

    return Response('Success', 200)


@view_config(route_name='machine_metadata', request_method='POST',
             renderer='json')
def set_machine_metadata(request):
    """Sets metadata for a machine, given the backend and machine id.

    Libcloud handles this differently for each provider. Linode and Rackspace,
    at least the old Rackspace providers, don't support metadata adding.

    machine_id comes as u'...' but the rest are plain strings so use == when
    comparing in ifs. u'f' is 'f' returns false and 'in' is too broad.

    """
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    if conn.type in [Provider.LINODE, Provider.RACKSPACE_FIRST_GEN]:
        return Response('Adding metadata is not supported in this provider',
                        501)

    machine_id = request.matchdict['machine']

    try:
        tag = request.json_body['tag']
        unique_key = 'mist.io_tag-' + datetime.now().isoformat()
        pair = {unique_key: tag}
    except:
        return Response('Malformed metadata format', 400)

    if conn.type in EC2_PROVIDERS:
        try:
            machine = Node(machine_id,
                           name='',
                           state=0,
                           public_ips=[],
                           private_ips=[],
                           driver=conn)
            conn.ex_create_tags(machine, pair)
        except:
            return Response('Error while creating tag in EC2', 503)
    else:
        try:
            nodes = conn.list_nodes()
            for node in nodes:
                if node.id == machine_id:
                    machine = node
                    break
        except:
            return Response('Machine not found', 404)

        try:
            machine.extra['metadata'].update(pair)
            conn.ex_set_metadata(machine, machine.extra['metadata'])
        except:
            return Response('Error while creating tag', 503)

    return Response('Success', 200)


@view_config(route_name='machine_metadata', request_method='DELETE',
             renderer='json')
def delete_machine_metadata(request):
    """Deletes metadata for a machine, given the machine id and the tag to be
    deleted.

    Libcloud handles this differently for each provider. Linode and Rackspace,
    at least the old Rackspace providers, don't support metadata updating. In
    EC2 you can delete just the tag you like. In Openstack you can only set a
    new list and not delete from the existing.

    Mist.io client knows only the value of the tag and not it's key so it
    has to loop through the machine list in order to find it.

    Don't forget to check string encoding before using them in ifs.
    u'f' is 'f' returns false.

    """
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    if conn.type in [Provider.LINODE, Provider.RACKSPACE_FIRST_GEN]:
        return Response('Updating metadata is not supported in this provider',
                        501)

    try:
        tag = request.json_body['tag']
    except:
        return Response('Malformed metadata format', 400)

    machine_id = request.matchdict['machine']

    try:
        nodes = conn.list_nodes()
        for node in nodes:
            if node.id == machine_id:
                machine = node
                break
    except:
        return Response('Machine not found', 404)

    if conn.type in EC2_PROVIDERS:
        tags = machine.extra.get('tags', None)
        try:
            for mkey, mdata in tags.iteritems():
                if tag == mdata:
                    pair = {mkey: tag}
                    break
        except:
            return Response('Tag not found', 404)

        try:
            conn.ex_delete_tags(machine, pair)
        except:
            return Response('Error while deleting metadata in EC2', 503)
    else:
        tags = machine.extra.get('metadata', None)
        try:
            for mkey, mdata in tags.iteritems():
                if tag == mdata:
                    tags.pop(mkey)
                    break
        except:
            return Response('Tag not found', 404)

        try:
            conn.ex_set_metadata(machine, tags)
        except:
            return Response('Error while updating metadata', 503)

    return Response('Success', 200)


def shell_command(request, backend_id, machine_id, host, command, ssh_user = None, key = None):
    """ Sends a command over ssh, using fabric """
    
    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})

    if not key:
        preferred_keypairs = get_preferred_keypairs(keypairs, backend_id, machine_id)
    else:
        preferred_keypairs = [key]

    for k in preferred_keypairs:
        keypair = keypairs[k]
        private_key = keypair.get('private', None)
        if private_key:
            if ssh_user == 'undefined':
                ssh_user = None
            ssh_user = ssh_user or get_ssh_user_from_keypair(keypair, 
                                                 backend_id, 
                                                 machine_id) or 'root'
              
            log.debug("before run command %s" % ssh_user)
            response = run_command(machine_id, 
                                   host, 
                                   ssh_user, 
                                   private_key, 
                                   command)
            cmd_output = response.text
            new_ssh_user = False
            if 'Please login as the user ' in cmd_output:
                new_ssh_user = cmd_output.split()[5].strip('"')
            elif 'Please login as the' in cmd_output:
                # for EC2 Amazon Linux machines, usually with ec2-user
                new_ssh_user = cmd_output.split()[4].strip('"')

            sudoer = False

            if new_ssh_user:
                response = run_command(machine_id, 
                                       host, 
                                       new_ssh_user, 
                                       private_key, 
                                       command)
                cmd_output = response.text
                ssh_user = new_ssh_user # update username in key-machine association
            
            if response.status_code != 200:
                # Mark key failure
                save_keypair(request, 
                             k, 
                             backend_id, 
                             machine_id, 
                             -1*int(time()), # minus means failure
                             ssh_user,
                             sudoer) 
                continue
            
            # TODO: Test if user is sudoer
            if command.startswith('sudo -n uptime 2>&1'):
                split_output = cmd_output.split('--------')
                try:
                    if int(split_output[0]) > 0:
                        sudoer = True
                except ValueError:
                    pass
            
            # Mark key success
            save_keypair(request, 
                         k, 
                         backend_id, 
                         machine_id, 
                         int(time()), 
                         ssh_user, 
                         sudoer)
            
            return {'output': cmd_output,
                    'ssh_user': ssh_user,
                    'sudoer': sudoer}
        
    return False


@view_config(route_name='probe', request_method='POST',
             renderer='json')
def probe(request):
    """Probes a machine over ssh, using fabric.

    .. note:: Used for getting uptime and a list of deployed keys.

    """
    machine_id = request.matchdict['machine']
    backend_id = request.matchdict['backend']
    host = request.params.get('host', None)
    key = request.params.get('key', None)
    if key == 'undefined':
        key = None

    ssh_user = request.params.get('ssh_user', None)
    command = "sudo -n uptime 2>&1|grep load|wc -l && echo -------- && cat /proc/uptime && echo -------- && cat ~/`grep '^AuthorizedKeysFile' /etc/ssh/sshd_config /etc/sshd_config 2> /dev/null|awk '{print $2}'` 2>/dev/null || cat ~/.ssh/authorized_keys 2>/dev/null"

    if key:
        log.warn('probing with key %s' % key)

    ret = shell_command(request, backend_id, machine_id, host, command, ssh_user, key)
    if ret:
        cmd_output = ret['output'].split('--------')

        if len(cmd_output) > 2:
            return {'uptime': cmd_output[1],
                    'updated_keys': update_available_keys(request, 
                                                          backend_id, 
                                                          machine_id, 
                                                          ssh_user, 
                                                          host, 
                                                          cmd_output[2]),
                   }
    
    return Response('No valid keys for server', 405)


@view_config(route_name='images', request_method='GET', renderer='json')
def list_images(request):
    """List images from each backend."""
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    try:
        if conn.type in EC2_PROVIDERS:
            images = conn.list_images(None, EC2_IMAGES[conn.type].keys())
            for image in images:
                image.name = EC2_IMAGES[conn.type][image.id]
        else:
            images = conn.list_images()
    except:
        return Response('Backend unavailable', 503)

    ret = []
    for image in images:
        ret.append({'id'    : image.id,
                    'extra' : image.extra,
                    'name'  : image.name,
                    })
    return ret


@view_config(route_name='sizes', request_method='GET', renderer='json')
def list_sizes(request):
    """List sizes (aka flavors) from each backend."""
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    try:
        sizes = conn.list_sizes()
    except:
        return Response('Backend unavailable', 503)

    ret = []
    for size in sizes:
        ret.append({'id'        : size.id,
                    'bandwidth' : size.bandwidth,
                    'disk'      : size.disk,
                    'driver'    : size.driver.name,
                    'name'      : size.name,
                    'price'     : size.price,
                    'ram'       : size.ram,
                    })

    return ret


@view_config(route_name='locations', request_method='GET', renderer='json')
def list_locations(request):
    """List locations from each backend.

    Locations mean different things in each backend. e.g. EC2 uses it as a
    datacenter in a given availability zone, whereas Linode lists availability
    zones. However all responses share id, name and country eventhough in some
    cases might be empty, e.g. Openstack.

    In EC2 all locations by a provider have the same name, so the availability
    zones are listed instead of name.

    """
    try:
        conn = connect(request)
    except:
        return Response('Backend not found', 404)

    try:
        locations = conn.list_locations()
    except:
        locations = [NodeLocation('', name='default', country='', driver=conn)]

    ret = []
    for location in locations:
        if conn.type in EC2_PROVIDERS:
            name = location.availability_zone.name
        else:
            name = location.name

        ret.append({'id'        : location.id,
                    'name'      : name,
                    'country'   : location.country,
                    })

    return ret


@view_config(route_name='keys', request_method='GET', renderer='json')
def list_keys(request):
    """List keys.

    List all key pairs that are configured on this server. Only the public
    keys are returned.

    """
    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})

    ret = [{'name': key,
            'machines': keypairs[key].get('machines', []),
            'pub': keypairs[key]['public'],
            'priv': keypairs[key]['private'] and True or False,
            'default_key': keypairs[key].get('default', False)}
           for key in keypairs.keys()]
    return ret


@view_config(route_name='keys', request_method='POST', renderer='json')
def update_keys(request):
    """Either generate a keypair or change the default one.

    generate_keys() in in a POST because it has computanional cost and it
    should not be exposed to everyone.

    """
    params = request.json_body

    if params['action'] == 'generate':
        ret = generate_keypair()
    elif params['action'] == 'set_default':
        try:
            ret = set_default_key(request)
        except KeyError:
            ret = Response('Key name not provided', 400)
    else:
        ret = Response('Keys action not supported', 405)

    return ret


@view_config(route_name='key', request_method='PUT', renderer='json')
def edit_key(request):
    """Creates or edits a keypair."""
    params = request.json_body
    key_id = params.get('name', '')
    old_id = params.get('oldname', '')

    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})
    
    if not key_id:
        ret = Response('Key name not provided', 400)

    key = {'public' : params.get('pub', ''),
            'private' : params.get('priv', '')}
    
    if old_id:
        if old_id != key_id:
            if key_id in keypairs:
                return Response('Key "%s" already exists' % key_id, 409)
            keypairs[key_id] = key
            keypairs[key_id]['machines'] = keypairs[old_id].get('machines', [])
            keypairs.pop(old_id)
        else:
            keypairs[key_id] = key
            keypairs[key_id]['machines'] = keypairs[old_id].get('machines', [])
    else:
        if key_id in keypairs:
            return Response('Key "%s" already exists' % key_id, 409)     
        keypairs[key_id] = key
    
    if key['public'] and key['private']:
        if not validate_key_pair(key['public'], key['private']):
            return Response('Key pair is not valid', 409)
        
    if len(keypairs) < 2:
        key['default'] = True
    
    log.debug('saving settings (edit key)')
    save_settings(request)
    
    ret = {'name': key_id,
           'pub': key['public'],
           'priv': key['private'],
           'default_key': key.get('default', False),
           'machines': keypairs[key_id].get('machines', [])}

    return ret


@view_config(route_name='key', request_method='POST', renderer='json')
def update_key(request):
    """Associate/disassociate a keypair with a machine, or get private key.

    """
    params = request.json_body
    if params['action'] in ['associate', 'disassociate']:
        key_id = params['key_id']
        backend_id = params['backend_id']
        machine_id = params['machine_id']
        if params['action'] == 'associate':
            ret = associate_key(request, key_id, backend_id, machine_id)
        else:
            ret = disassociate_key(request, key_id, backend_id, machine_id)
    elif params['action'] == 'get_private_key':
        ret = get_private_key(request)
    #elif params['action'] == 'associate_ssh_user': #TODO: test
    #    key_id = params['key_id']
    #    ssh_user = params['ssh_user']
    #    backend_id = params['backend_id']
    #    machine_id = params['machine_id']
    #    ret = save_keypair(request, key_id, ssh_user, backend_id, machine_id)
    else:
        ret = Response('Key action not supported', 405)

    return ret


@view_config(route_name='key', request_method='DELETE', renderer='json')
def delete_key(request):
    """Deletes a keypair.

    When a key gets deleted it takes its asociations with it so just need to
    remove form the server too.

    If the default key gets deleted, it sets the next one as default, provided
    that at least another key exists. It returns the list of all keys after
    the deletion, excluding the private keys (check also list_keys).


    """
    params = request.json_body

    try:
        key_id = params['key_id']
    except KeyError:
        return Response('Key name not provided', 400)

    keypairs = request.registry.settings['keypairs']
    key = keypairs.pop(key_id)

    try:
        #TODO: alert user for key undeployment
        #for machine in key.get('machines', []):
        #    undeploy_key(request, machine[0], machine[1], key)
        ret_code = 200
    except:
        ret_code = 206

    if key.get('default', None):
        try:
           new_default_key = keypairs.keys()[0]
           keypairs[new_default_key]['default'] = True
        except IndexError:
            pass

    log.debug('saving settings (del key)')
    save_settings(request)

    ret = [{'name': key,
            'machines': keypairs[key].get('machines', False),
            'pub': keypairs[key]['public'],
            'default_key': keypairs[key].get('default', False)}
           for key in keypairs.keys()]

    return ret


@view_config(route_name='monitoring', request_method='GET', renderer='json')
def check_monitoring(request):
    """Ask the mist.io service if monitoring is enabled for this machine.

    """
    core_uri = request.registry.settings['core_uri']
    email = request.registry.settings.get('email','')
    password = request.registry.settings.get('password','')

    timestamp = datetime.utcnow().strftime("%s")
    hash = sha256("%s:%s:%s" % (email, timestamp, password)).hexdigest()

    payload = {'email': email,
               'timestamp': timestamp,
               'hash': hash,
               }

    ret = requests.get(core_uri+request.path, params=payload, verify=False)
    if ret.status_code == 200:
        return ret.json()
    else:
        return Response('Service unavailable', 503)


@view_config(route_name='update_monitoring', request_method='POST', renderer='json')
def update_monitoring(request):
    """Enable/disable monitoring for this machine using the hosted mist.io
    service.

    """
    core_uri = request.registry.settings['core_uri']
    try:
        email = request.json_body['email']
        password = request.json_body['pass']
        timestamp = request.json_body['timestamp']
        hash = request.json_body['hash']       
    except:
        email = request.registry.settings.get('email','')
        password = request.registry.settings.get('password','')
        timestamp =  datetime.utcnow().strftime("%s")
        hash = sha256("%s:%s:%s" % (email, timestamp, password)).hexdigest()

    name = request.json_body.get('name','')
    public_ips = request.json_body.get('public_ips', [])
    dns_name = request.json_body.get('dns_name', '')
    
    action = request.json_body['action'] or 'enable'
    payload = {'email': email,
               'timestamp': timestamp,
               'hash': hash,
               'action': action,
               'name': name,
               'public_ips': public_ips,
               'dns_name': dns_name,
               }

    if action == 'enable':
        backend = request.registry.settings['backends'][request.matchdict['backend']]
        payload['backend_title'] = backend['title']
        payload['backend_provider'] = backend['provider']
        payload['backend_region'] = backend['region']
        payload['backend_apikey'] = backend['apikey']
        payload['backend_apisecret'] = backend['apisecret']

    #TODO: make ssl verification configurable globally, set to true by default
    ret = requests.post(core_uri+request.path, params=payload, verify=False)

    if ret.status_code == 402:
        return Response(ret.text, 402)
    elif ret.status_code != 200:
        return Response('Service unavailable', 503)

    request.registry.settings['email'] = email
    request.registry.settings['password'] = password
    request.registry.settings['auth'] = 1
    log.debug('saving settings (update monitoring)')
    save_settings(request)
    return ret.json()


@view_config(route_name='rules', request_method='POST', renderer='json')
def update_rule(request):
    """Creates or updates a rule.

    """
    core_uri = request.registry.settings['core_uri']
    email = request.registry.settings.get('email','')
    password = request.registry.settings.get('password','')
    timestamp =  datetime.utcnow().strftime("%s")
    hash = sha256("%s:%s:%s" % (email, timestamp, password)).hexdigest()

    payload = request.json_body.copy()
    payload['email'] = email
    payload['hash'] = hash
    payload['timestamp'] = timestamp

    #TODO: make ssl verification configurable globally, set to true by default
    ret = requests.post(core_uri+request.path, params=payload, verify=False)

    if ret.status_code != 200:
        return Response('Service unavailable', 503)

    return ret.json()


@view_config(route_name='rule', request_method='DELETE')
def delete_rule(request):
    """Deletes a rule.

    """
    # TODO: factor out common code in a shared function
    core_uri = request.registry.settings['core_uri']
    email = request.registry.settings.get('email','')
    password = request.registry.settings.get('password','')
    timestamp =  datetime.utcnow().strftime("%s")
    hash = sha256("%s:%s:%s" % (email, timestamp, password)).hexdigest()

    payload = {}
    payload['email'] = email
    payload['hash'] = hash
    payload['timestamp'] = timestamp

    #TODO: make ssl verification configurable globally, set to true by default
    ret = requests.delete(core_uri+request.path, params=payload, verify=False)

    if ret.status_code != 200:
        return Response('Service unavailable', 503)

    return Response('OK', 200)


def update_available_keys(request, backend_id, machine_id, ssh_user, host, authorized_keys):
    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})
    
    # track which keypairs will be updated
    updated_keypairs = {}
    
    # get the actual public keys from the blob
    ak = [k for k in authorized_keys.split('\n') if k.startswith('ssh')]

    # for each public key
    for pk in ak:
        exists = False
        pub_key = pk.strip().split(' ')
        for k in keypairs:
            # check if the public key already exists in our keypairs 
            if keypairs[k]['public'].strip().split(' ')[:2] == pub_key[:2]:
                exists = True
                associated = False
                # check if it is already associated with this machine
                for m in keypairs[k].get('machines', []):
                    if m[:2] == [backend_id, machine_id]:
                        associated = True
                        break
                if not associated:
                    if not keypairs[k].get('machines', None):
                        keypairs[k]['machines'] = []
                    keypairs[k]['machines'].append([backend_id, machine_id])
                    updated_keypairs[k] = keypairs[k]
            if exists:
                break
                    
        # if public key does not exist in our keypairs, add a new entry
        if not exists:
            if len(pub_key)>2:
                key_name = pub_key[2].strip('\r')
            else:
                key_name = "%s@%s" % (ssh_user, host)
                if key_name in keypairs:
                    i = 0
                    while True:
                        key_name = '%s@%s-%d' % (ssh_user, host, i)
                        i+=1
                        if key_name not in keypairs:
                            break
            keypairs[key_name] = {'public': ' '.join(pk.split(' ')[:2]),
                                  'private': '',
                                  'machines': [[backend_id, machine_id, 0, ssh_user]]}
            updated_keypairs[key_name] = keypairs[key_name]

    if updated_keypairs:
        log.debug('update keypairs')
        save_settings(request)

    ret = [{'name': key,
            'machines': keypairs[key].get('machines', []),
            'pub': keypairs[key]['public'],
            'default_key': keypairs[key].get('default', False)}
           for key in updated_keypairs.keys()]
     
    return ret


def save_keypair(request, key_id, backend_id, machine_id, timestamp, ssh_user, sudoer, public_key = False, private_key = False, default = None):
    """ Updates an ssh keypair or associates an ssh user for a machine with a key.

    """
    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})

    if key_id not in keypairs:
        keypairs[key_id] = {'machines': []}
    
    keypair = keypairs[key_id]

    if public_key:
        keypair['public'] = public_key

    if private_key:
        keypair['private'] = private_key

    if default != None:
        keypair['default'] = default

    log.debug("Keypair is : %s" % keypair)
    for machine in keypair.get('machines',[]):
        if [backend_id, machine_id] == machine[:2]:
            keypairs[key_id]['machines'][keypair['machines'].index(machine)] = [backend_id, machine_id, timestamp, ssh_user, sudoer]
        else:
            log.debug("Machines are : %s" % keypair.get('machines', []))

    try:
        log.debug('save settings (save keypair)')
        save_settings(request)
    except Exception, e:
        log.error('Error saving keypair %s: %s' % (key_id, e))
        return False
        
    return True


def associate_key(request, key_id, backend_id, machine_id, deploy=True):
    """Associates a key with a machine.

    If deploy is set to True it will also attempt to actually deploy it to the
    machine.

    """
    log.debug("Associate key, deploy = %s" % deploy)
    if not key_id or not machine_id or not backend_id:
        return Response('Keypair, machine or backend not provided', 400)

    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})

    try:
        keypair = keypairs[key_id]
    except KeyError:
        return Response('Keypair not found', 404)

    machine_uid = [backend_id, machine_id]
    machines = keypair.get('machines', [])
    
    for machine in machines:
        if machine[:2] == machine_uid:
            return Response('Keypair already associated to machine', 304)

    try:
        keypair['machines'].append(machine_uid)
    except KeyError: 
        # initialize machine associations array if it does not exist
        keypair['machines'] = [machine_uid]

    if deploy:
        ret = deploy_key(request, keypair)
    
        if ret:
            keypair['machines'][-1] += [int(time()), ret.get('ssh_user', ''), ret.get('sudoer', False)]
            log.debug('save settings (associate key)')
            save_settings(request)
            log.debug("Associate key, %s" % keypair['machines'])
            return Response('OK', 200)
        else:
            if machine_uid in keypair['machines']:
                keypair['machines'].remove(machine_uid)
            log.debug("Associate key, %s" % keypair['machines'])
            
            return Response('Failed to deploy key', 412)
    else:
        log.debug('save settings (associate key2)')
        log.debug("deploy false")
        save_settings(request)
        return Response('OK', 200)


def disassociate_key(request, key_id, backend_id, machine_id, undeploy=True):
    """Disassociates a key from a machine.

    If undeploy is set to True it will also attempt to actually remove it from
    the machine.

    """
    if not key_id or not machine_id or not backend_id:
        return Response('Keypair, machine or backend not provided', 400)

    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})

    try:
        keypair = keypairs[key_id]
    except KeyError:
        return Response('Keypair not found', 404)

    machine_uid = [backend_id, machine_id]
    machines = keypair.get('machines', [])

    key_found = False
    for machine in machines:
        if machine[:2] == machine_uid:
            keypair['machines'].remove(machine)
            key_found = True
            break

    #key not associated
    if not key_found: 
        return Response('Keypair is not associated to this machine', 304)

    if undeploy:
        ret = undeploy_key(request, keypair)

    log.debug('save settings (disassociate key)')
    save_settings(request)

    return Response('OK', 200)


def deploy_key(request, keypair):
    """Deploys the provided keypair to the machine.

    To do that it requires another keypair (existing_key) that can connect to
    the machine.

    """
    grep_output = '`grep \'%s\' ~/.ssh/authorized_keys`' % keypair['public']
    command = 'if [ -z "%s" ]; then echo "%s" >> ~/.ssh/authorized_keys; fi' % (grep_output, keypair['public'])
    host = request.json_body.get('host', None)
    backend_id = request.json_body.get('backend_id', None)
    machine_id = request.json_body.get('machine_id', None)
    
    try:
        ret = shell_command(request, backend_id, machine_id, host, command)
    except:
        pass

    # Maybe the deployment failed but let's try to connect with the new key and see what happens
    try:
        keypairs = request.environ['beaker.session']['keypairs']
    except:
        keypairs = request.registry.settings.get('keypairs', {})
    
    key_name = None
    for key_name, k in keypairs.items():
        if k == keypair:
            break

    if key_name:
        log.warn('probing with key %s' % key_name)

    if ret:
        ssh_user = ret.get('ssh_user', None)
    else:
        ssh_user = None

    test = shell_command(request, backend_id, machine_id, host, 'whoami', ssh_user, key = key_name)

    return test


def undeploy_key(request, keypair):
    """Removes the provided keypair from the machine.

    It connects to the server with the key that is supposed to be deleted.

    """
    command = 'grep -v "' + keypair['public'] + '" ~/.ssh/authorized_keys ' +\
              '> ~/.ssh/authorized_keys.tmp && ' +\
              'mv ~/.ssh/authorized_keys.tmp ~/.ssh/authorized_keys ' +\
              '&& chmod go-w ~/.ssh/authorized_keys'
    host = request.json_body.get('host', None)
    backend_id = request.json_body.get('backend_id', None)
    machine_id = request.json_body.get('machine_id', None)
                  
    try:
        ret = shell_command(request, backend_id, machine_id, host, command)
    except:
        return False

    return ret

