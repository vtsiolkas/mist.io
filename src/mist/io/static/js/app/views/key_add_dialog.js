define('app/views/key_add_dialog', [
    'text!app/templates/key_add_dialog.html','ember'],
    /**
     *
     * Add Key dialog
     *
     * @returns Class
     */
    function(key_add_dialog_html) {
        return Ember.View.extend({
            
            attributeBindings: ['data-role',],
            
            notEditMode: function() {
                return this.get('parentView').toString().indexOf('SingleKeyView') == -1;
            }.property('notEditMode'),
            
            getAssociatedMachine: function() {
                var machine;
                try {
                    machine = this.get('parentView').get('controller').get('model');
                } catch (e) {}
                return machine;
            },
            
            backClicked: function() {
                $("#dialog-add-key").popup("close");
                Mist.keyAddController.newKeyClear();
                if (this.getAssociatedMachine()){
                    setTimeout( function(){
                            $('#associate-key-dialog').popup('option', 'positionTo', '#associate-key-button').popup('open');
                    }, 350); 
                }
            },

            generateClicked: function() {
                $('#dialog-add-key .ajax-loader').fadeIn(200);
                var payload = {
                    'action': 'generate'
                };
                $.ajax({
                    url: '/keys',
                    type: "POST",
                    data: JSON.stringify(payload),
                    contentType: "application/json",
                    headers: { "cache-control": "no-cache" },
                    dataType: "json",
                    success: function(result) {
                        Mist.keyAddController.set('newKeyPublic', result.public);
                        Mist.keyAddController.set('newKeyPrivate', result.private);
                        $('#dialog-add-key .ajax-loader').hide();
                    }
                });
            },

            uploadClicked: function(keyType) {
                if (window.File && window.FileReader && window.FileList) {
                    $("#upload-" + keyType + "-key-input").click();
                } else {
                    alert('The File APIs are not fully supported in this browser.');
                }
            },
            
            uploadInputChanged: function(keyType) {
                var reader = new FileReader();
                reader.onloadend = function(evt) {
                    if (evt.target.readyState == FileReader.DONE) {
                        $('#textarea-' + keyType + '-key').val(evt.target.result).trigger('change');
                     }
               };
               reader.readAsText($('#upload-' + keyType + '-key-input')[0].files[0], 'UTF-8');
            },
            
            newKeyClicked: function() {
                var publicKey = $('#textarea-public-key').val().trim();
                var publicKeyType = "";     
                if (publicKey) {
                    if (publicKey.indexOf('ssh-rsa') != 0 && publicKey.indexOf('ssh-dss') != 0) {
                        Mist.notificationController.notify('Public key should begin with "ssh-rsa" or "ssh-dss"');
                        return;
                    } else if (publicKey.indexOf('ssh-rsa') == 0) {
                        publicKeyType = 'RSA';
                    } else {
                        publicKeyType = 'DSA';
                    }
                }
                var privateKey = $('#textarea-private-key').val().trim();
                if (privateKey) {   
                    var privateKeyType = privateKey.substring('-----BEGIN '.length , '-----BEGIN '.length + 3);
                    if (privateKeyType != 'RSA' && privateKeyType != 'DSA') {
                        Mist.notificationController.notify('Unknown ssh type of private key');
                        return;   
                    } else if (publicKey && publicKeyType != privateKeyType) {
                        Mist.notificationController.notify("Key pair ssh types don't match");
                        return;
                    }  
                    var beginning = '-----BEGIN ' + privateKeyType + ' PRIVATE KEY-----';
                    var ending = '-----END ' + privateKeyType + ' PRIVATE KEY-----';
                    if (privateKey.indexOf(beginning) != 0) {
                        Mist.notificationController.notify('Private key should begin with ' + beginning);
                        return;
                    } else if (privateKey.indexOf(ending) != privateKey.length - ending.length) {
                        Mist.notificationController.notify('Private key should end with ' + ending);
                        return;
                    }
                } else {
                    Mist.notificationController.notify('Private key required');
                    return;
                }
                
                if (this.get('notEditMode')) {
                    var machine = this.getAssociatedMachine();
                    if (machine) {
                        $('#manage-keys .ajax-loader').fadeIn(200);
                    }
                    Mist.keyAddController.newKey(machine);
                } else {
                    Mist.keyAddController.editKey(this.get('parentView').get('controller').get('model').name);
                }
            },
            
            template: Ember.Handlebars.compile(key_add_dialog_html)

        });
    }
);
